"""Mandatory lead admission, independent of model instructions or CRM tool use."""
from __future__ import annotations

import re

from . import api

LEAD_GATE_REVISION = "internal-contact-v2"


def event_deal_seq(payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    deal = data.get("deal") if isinstance(data, dict) else None
    seq = deal.get("seq") if isinstance(deal, dict) else None
    if type(seq) is int:
        return seq if seq > 0 else None
    if isinstance(seq, str) and re.fullmatch(r"[0-9]{1,19}", seq):
        value = int(seq)
        return value if value > 0 else None
    return None


def check_lead(*, api_key, base_url, seq, contact):
    """Return an ignore reason, or None for a verified lead belonging to this contact.

    Only a definite 404 means absent. Authentication, transport and invalid API
    responses raise separately; callers must fail closed without replying.
    """
    try:
        envelope = api.request_json(api_key=api_key, base_url=base_url, method="GET",
                                    path=f"/api/v1/deals/{seq}")
    except api.PipefacilAPIError as exc:
        if exc.status_code == 404:
            return "lead_not_found"
        raise
    lead = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(lead, dict) or type(lead.get("seq")) is not int or lead["seq"] != seq:
        raise api.PipefacilAPIError("Pipefacil returned an invalid lead admission response.")
    owner = lead.get("contact")
    if not isinstance(owner, dict) or not owner.get("id"):
        return "lead_contact_mismatch"
    if not contact.get("id") or owner["id"] != contact["id"]:
        return "lead_contact_mismatch"
    def phone(value):
        return re.sub(r"\D", "", str(value or ""))
    if not phone(owner.get("phone")) or phone(owner.get("phone")) != phone(contact.get("phone")):
        return "lead_contact_mismatch"
    return None
