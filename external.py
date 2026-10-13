"""Authenticated external webhooks can use CRM execution v2 without profile management."""

import asyncio
import logging
from queue import Full
import sqlite3
import time

from aiohttp import web

from .managed import validate_payload
from .state import Conflict, StateError

logger = logging.getLogger(__name__)


async def accept_webhook(adapter, payload):
    # Only the HMAC-verified ingress calls this function. This marker is generated
    # locally and forbidden in incoming JSON; it grants no administration access.
    max_age = min(adapter.max_message_age_seconds, 600)
    try:
        execution, data = validate_payload(adapter, payload, max_age=max_age, allow_team_messages=True)
    except ValueError as exc:
        if str(exc) == "expired_execution":
            return web.json_response({"status": "ignored", "reason": "expired_execution"})
        return web.json_response({"error": str(exc)}, status=403 if str(exc) == "channel_not_allowed" else 422)
    chat = ":".join((execution["channelId"], execution["conversationId"]))
    kwargs = dict(payload={**payload, "_pipefacil_external_execution": {"execution": execution}},
                  messages=[m for m in data["messages"] if m["fromMe"] is False],
                  contact=data["contact"], channel=data["channel"],
                  chat_id=chat, phone=data["contact"]["phone"])
    try:
        job = await asyncio.to_thread(adapter.state.admit, kwargs, now=time.time(), max_age=max_age)
    except Full:
        return web.json_response({"error": "inbound_queue_full"}, status=429, headers={"Retry-After": "5"})
    except Conflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (OSError, sqlite3.Error, StateError):
        logger.exception("[pipefacil] External execution could not be persisted; refused admission")
        return web.json_response({"error": "inbound receipt storage unavailable"}, status=503)
    if job is None:
        adapter._counts["duplicate"] += 1
        return web.json_response({"status": "duplicate"})
    adapter._counts["admitted"] += 1
    adapter._schedule(chat)
    return web.json_response({"status": "accepted", "job_id": str(job)})
