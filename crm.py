"""Fresh GET authorization and PATCH confirmation for this turn's deal only."""

import math
import re
from datetime import datetime

from . import api
from .state import canonical


class CRM:
    def __init__(self, adapter, *, member_user_id, fields, custom_fields, stages):
        self.adapter = adapter
        self.member_user_id = member_user_id
        self.fields = frozenset(fields)
        self.custom_fields = frozenset(custom_fields)
        self.stages = frozenset(stages)

    def authorized(self, context, key, *, writing=False):
        seq = context.get("deal_seq")
        if not isinstance(seq, int) or seq <= 0:
            raise api.PipefacilAPIError("The current Pipefacil event has no deal to update.")
        if writing and not self.member_user_id:
            raise api.PipefacilAPIError("Deal updates require PIPEFACIL_MEMBER_USER_ID for this profile.")
        lead = api.get_deal(api_key=key, base_url=self.adapter.api_base_url, seq=seq)
        if not isinstance(lead, dict) or lead.get("seq") != seq:
            raise api.PipefacilAPIError("The deal does not match this conversation.")
        contact = lead.get("contact") or {}
        expected = context["contact"]
        def phone(value):
            return re.sub(r"\D", "", str(value or ""))
        if (not isinstance(contact, dict) or not contact.get("id")
                or expected.get("id") and contact.get("id") != expected["id"]
                or not phone(contact.get("phone")) or phone(contact.get("phone")) != phone(expected.get("phone"))):
            raise api.PipefacilAPIError("The deal no longer belongs to this conversation.")
        # The backend legitimately delivers conversations with no open deals, or
        # legacy agents without a member. Preserve those chats; writes always need
        # an operator-configured user id and current ownership.
        if self.member_user_id and (writing or str(lead.get("status")).lower() == "open"):
            if lead.get("responsibleUserId") != self.member_user_id:
                raise api.PipefacilAPIError("The deal is no longer assigned to this agent.")
        return lead

    def allowed_stages(self, lead, key):
        pipelines = api.get_pipelines(api_key=key, base_url=self.adapter.api_base_url)
        return [{"id": s["id"], "name": s.get("name"), "isLost": bool(s.get("isLost")), "isWon": bool(s.get("isWon"))}
                for p in pipelines if p.get("id") == lead.get("pipelineId") and not p.get("isArchived")
                for s in p.get("stages", []) if isinstance(s, dict) and s.get("id") in self.stages][:200]

    def read(self, context, key):
        lead = self.authorized(context, key)
        return {"seq": lead["seq"], "name": lead.get("name"), "notes": lead.get("notes"),
                "stageId": lead.get("stageId"), "status": lead.get("status"),
                "customFields": {k: v for k, v in (lead.get("properties") or {}).items() if k in self.custom_fields},
                "writableFields": sorted(self.fields) if self.member_user_id else [],
                "writableCustomFields": sorted(self.custom_fields) if self.member_user_id else [],
                "allowedStages": self.allowed_stages(lead, key) if self.member_user_id else []}

    def validate(self, properties):
        if not isinstance(properties, dict) or not properties or set(properties) - self.fields:
            raise ValueError("Unsupported or disabled deal fields.")
        if len(canonical(properties).encode()) > 32768:
            raise ValueError("Deal update exceeds 32 KiB.")
        limits = {"notes": 2000, "name": 255, "lostReason": 255, "currency": 3, "stageId": 100}
        for field, limit in limits.items():
            if field in properties and (not isinstance(properties[field], str) or len(properties[field]) > limit
                                        or "\x00" in properties[field]):
                raise ValueError("Invalid " + field)
        if "value" in properties and (type(properties["value"]) not in {int, float}
                                       or not math.isfinite(properties["value"]) or properties["value"] < 0):
            raise ValueError("Invalid value.")
        if "closeProbability" in properties and (type(properties["closeProbability"]) is not int
                                                  or not 0 <= properties["closeProbability"] <= 100):
            raise ValueError("Invalid closeProbability.")
        if "expectedCloseAt" in properties:
            value = properties["expectedCloseAt"]
            if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("expectedCloseAt requires an ISO timestamp with timezone.")
        fields = properties.get("customFields", {})
        if not isinstance(fields, dict) or set(fields) - self.custom_fields:
            raise ValueError("Custom field is not allowed for this profile.")
        for value in fields.values():
            if value is not None and type(value) not in {str, bool, int, float}:
                raise ValueError("Unsupported custom field value.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Invalid custom field value.")
        if "contact" in properties:
            contact = properties["contact"]
            if (not isinstance(contact, dict) or not contact or set(contact) - {"name", "email"}
                    or any(not isinstance(v, str) or len(v) > 255 or "\x00" in v for v in contact.values())):
                raise ValueError("Only contact name/email may be edited; the destination phone is immutable.")
        if "tagIds" in properties:
            tags = properties["tagIds"]
            if not isinstance(tags, list) or len(tags) > 50 or any(not isinstance(v, str) or not 1 <= len(v) <= 100 for v in tags):
                raise ValueError("Invalid tagIds.")

    def update(self, context, key, properties, active):
        self.validate(properties)
        lead = self.authorized(context, key, writing=True)
        if "stageId" in properties:
            matches = [s for s in self.allowed_stages(lead, key) if s["id"] == properties["stageId"]]
            if len(matches) != 1:
                raise ValueError("Stage is not allowed in the current pipeline.")
            if matches[0]["isLost"] and not properties.get("lostReason"):
                raise ValueError("A lost stage requires lostReason.")

        def write():
            api.update_deal(api_key=key, base_url=self.adapter.api_base_url, seq=lead["seq"], properties=properties)
            after = self.authorized(context, key, writing=True)
            for field, value in properties.items():
                actual = after.get(field)
                if field == "customFields":
                    if any((after.get("properties") or {}).get(k) != v for k, v in value.items()):
                        raise api.PipefacilAPIError("Deal update could not be confirmed.")
                elif field == "tagIds":
                    if {t["id"] for t in after.get("tags", [])} != set(value):
                        raise api.PipefacilAPIError("Deal tags could not be confirmed.")
                elif field == "contact":
                    detail = api.get_contact(api_key=key, base_url=self.adapter.api_base_url, contact_id=after["contact"]["id"])
                    if any(detail.get(k) != v for k, v in value.items()):
                        raise api.PipefacilAPIError("Contact update could not be confirmed.")
                elif field == "expectedCloseAt":
                    if not isinstance(actual, str) or datetime.fromisoformat(actual.replace("Z", "+00:00")) != datetime.fromisoformat(value.replace("Z", "+00:00")):
                        raise api.PipefacilAPIError("Deal timestamp could not be confirmed.")
                elif actual != value:
                    raise api.PipefacilAPIError("Deal update could not be confirmed.")
            return {"success": True, "updated": True, "seq": lead["seq"], "updated_fields": sorted(properties)}

        return self.adapter.effect(context["job_id"], "update_deal", properties, write, active=active)
