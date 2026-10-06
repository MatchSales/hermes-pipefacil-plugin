"""Ordered, terminal handoff shared by every SDR; commercial policy stays in the profile."""

import asyncio
import copy

from .api import PipefacilAPIError
from .state import digest


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
