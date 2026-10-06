"""Ordered, terminal handoff shared by every SDR; commercial policy stays in the profile."""

import asyncio
import copy

from .api import PipefacilAPIError
from .state import digest


def transfer_responsibility(*, api_key, base_url, expected, responsible_user_id):
    """Shared last-write primitive for a live turn or a separately journaled business outbox.

    The caller must authorize the current lead and durably reserve the attempt first.
    This primitive performs only the final owner PATCH, verifies its receipt, and never GETs.
    """
    from . import api

    if (set(expected) != {"seq", "contactId", "pipelineId", "stageId"}
            or type(expected["seq"]) is not int or expected["seq"] <= 0
            or any(not isinstance(expected[k], str) or not expected[k] for k in ("contactId", "pipelineId", "stageId"))
            or not isinstance(responsible_user_id, str) or not responsible_user_id):
        raise ValueError("Transfer requires a verified lead identity and an operator-configured responsible user.")
    try:
        envelope = api.update_deal(api_key=api_key, base_url=base_url, seq=expected["seq"],
                                   properties={"responsibleUserId": responsible_user_id})
    except PipefacilAPIError as exc:
        raise PipefacilAPIError("Handoff outcome is uncertain; operator reconciliation required: " + str(exc)) from None
    receipt = envelope.get("data") if isinstance(envelope, dict) else None
    verified = {**expected, "responsibleUserId": responsible_user_id, "status": "open"}
    if (not isinstance(receipt, dict) or envelope.get("success") is False or receipt.get("success") is False
            or any(receipt.get(k) != v for k, v in verified.items())):
        raise PipefacilAPIError("Handoff receipt is uncertain; operator reconciliation required.")
    return verified


async def execute(adapter, chat_id, context, key, args, active):
    if not isinstance(args, dict) or set(args) - {"properties", "message"}:
        raise ValueError("Handoff accepts only final properties and an optional closing message.")
    properties = copy.deepcopy(args.get("properties", {}))
    if not isinstance(properties, dict):
        raise ValueError("Handoff properties must be an object.")
    message = args.get("message")
    if message is not None and (not isinstance(message, str) or not message.strip()
                                or len(message) > 4000 or "\x00" in message):
        raise ValueError("Closing message must contain 1–4000 characters.")
    arguments = {"seq": context["deal_seq"], "contact": context["contact"],
                 "responsibleUserId": adapter.crm.handoff_user_id, "properties": properties, "message": message}
    if not active():
        raise PipefacilAPIError("The Pipefacil turn is no longer active.")
    terminal = adapter.state.handoff(context["job_id"])
    if terminal:
        if terminal["key"] == digest([context["job_id"], "handoff", arguments]) and terminal["state"] == "accepted":
            return terminal["result"]
        raise PipefacilAPIError("Handoff already attempted; operator reconciliation required, no request repeated.")
    prepared = await asyncio.to_thread(adapter.crm.prepare_handoff, context, key, properties, active)
    if message is not None:
        outbound = {"type": "text", "text": message}
        receipt = await adapter.send_api_message(chat_id, outbound)
        adapter.record_preliminary_delivery(chat_id, outbound, receipt)
    return await asyncio.to_thread(adapter.crm.transfer_handoff, context, key, prepared, properties, arguments, active)
