"""Execution v2 for managed profiles. Transport identity is never a model argument."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import re
import time
from urllib.parse import quote
from weakref import WeakValueDictionary

from . import api
from .inbox import fresh_messages, message_epoch
from .state import digest

CURRENT = ContextVar("pipefacil_managed_execution", default=None)


def current():
    return CURRENT.get()


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value)


def validate(adapter, command):
    if not isinstance(command, dict) or set(command) != {"workspaceId", "agentId", "profile", "generation", "revision", "payload"}:
        raise ValueError("invalid_execution")
    identity_path = adapter.profile_home / ".pipefacil-runtime.json"
    if identity_path.is_symlink():
        raise ValueError("invalid_identity")
    identity = json.loads(identity_path.read_text())
    if any(command.get(k) != identity.get(k) for k in ("workspaceId", "agentId", "profile", "generation", "revision")):
        raise ValueError("stale_profile")
    publication = adapter._managed_publication or {}
    if command["revision"] != publication.get("revision"):
        raise ValueError("stale_revision")
    payload = command["payload"]
    execution = payload.get("execution", {}) if isinstance(payload, dict) else {}
    if (execution.get("protocolVersion") != 2 or execution.get("agentId") != command["agentId"]
            or any(not _identifier(execution.get(k)) for k in ("executionId", "agentId", "dealId", "channelId", "conversationId"))
            or type(execution.get("generation")) is not int or type(execution.get("agentVersion")) is not int
            or payload.get("type") != "message.received"):
        raise ValueError("invalid_execution")
    data = payload.get("data", {})
    channel, contact, deal = data.get("channel", {}), data.get("contact", {}), data.get("deal", {})
    if (channel.get("id") != execution["channelId"] or deal.get("id") != execution["dealId"]
            or type(deal.get("seq")) is not int or deal["seq"] < 1
            or not _identifier(contact.get("id")) or not re.fullmatch(r"[+0-9]{5,30}", str(contact.get("phone", "")))):
        raise ValueError("invalid_destination")
    messages = data.get("messages")
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 100
            or any(not isinstance(m, dict) or not _identifier(m.get("id")) or m.get("fromMe") is not False for m in messages)):
        raise ValueError("invalid_messages")
    expires = message_epoch(execution.get("expiresAt"))
    if expires is None or expires <= time.time() or len(fresh_messages(messages, now=time.time(), max_age=600)) != len(messages):
        raise ValueError("expired_execution")
    if adapter.channel_ids and execution["channelId"] not in adapter.channel_ids:
        raise ValueError("channel_not_allowed")
    return identity, execution, data


async def accept(adapter, command):
    # Look up the immutable execution receipt before checking admission: retries after pause
    # can observe a previous result, but cannot enqueue a new turn.
    identity, execution, data = validate(adapter, command)
    existing = adapter.state.execution_receipt(execution["executionId"])
    if existing:
        adapter.state.verify_execution(execution["executionId"], command)
        return existing
    if adapter._closing or adapter._ready_error or not callable(adapter._message_handler):
        raise RuntimeError("runtime_unavailable")
    if adapter.management_status()["closed"]:
        raise RuntimeError("admission_closed")
    payload = {**command["payload"], "_pipefacil_managed": {**identity, "execution": execution}}
    chat = ":".join((execution["channelId"], execution["conversationId"]))
    kwargs = dict(payload=payload, messages=data["messages"], contact=data["contact"],
                  channel=data["channel"], chat_id=chat, phone=data["contact"]["phone"])
    await asyncio.to_thread(adapter.state.admit, kwargs, now=time.time(), max_age=600)
    adapter._schedule(chat)
    return adapter.state.execution_receipt(execution["executionId"])


@asynccontextmanager
async def turn_scope(adapter, kwargs):
    value = kwargs["payload"].get("_pipefacil_managed") if adapter.management_status()["managed"] else None
    if value is None:
        yield
        return
    locks = getattr(adapter, "_managed_deal_locks", None)
    if locks is None:
        locks = adapter._managed_deal_locks = WeakValueDictionary()
    key = (value["workspaceId"], value["execution"]["dealId"])
    lock = locks.setdefault(key, asyncio.Lock())
    async with lock:
        token = CURRENT.set(value)
        try:
            yield
        finally:
            CURRENT.reset(token)


def context(*, api_key, base_url):
    value = current()
    if value is None:
        raise api.PipefacilAPIError("No managed execution.")
    execution = value["execution"]
    data = api.request_json(api_key=api_key, base_url=base_url, method="GET",
            path="/api/v1/ai-agent/executions/" + quote(execution["executionId"], safe=""))["data"]
    received = data.get("execution", {}) if isinstance(data, dict) else {}
    if any(received.get(k) != execution.get(k) for k in ("protocolVersion", "executionId", "agentId", "dealId",
                                                        "generation", "agentVersion", "channelId", "conversationId")):
        raise api.PipefacilAPIError("Execution context mismatch.")
    return data


def effect(*, api_key, base_url, method, suffix, payload=None):
    execution = current()["execution"]
    key = digest([execution["executionId"], method, suffix, payload])
    receipt_path = "/api/v1/ai-agent/receipts/" + key

    def receipt():
        try:
            return api.request_json(api_key=api_key, base_url=base_url, method="GET", path=receipt_path)["data"]
        except api.PipefacilAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    result = receipt()
    if result is None:
        try:
            result = api.request_json(api_key=api_key, base_url=base_url, method=method,
                    path="/api/v1/ai-agent/executions/" + quote(execution["executionId"], safe="") + suffix,
                    payload=payload, idempotency_key=key)["data"]
        except api.PipefacilAPIError:
            # GET only after uncertainty. Even a 404 here is not permission to repeat the POST.
            result = receipt()
            if result is None:
                raise api.PipefacilAPIError("Effect outcome uncertain; do not repeat.") from None
    if not isinstance(result, dict) or result.get("status") != "APPLIED" or result.get("result") is None:
        raise api.PipefacilAPIError("Effect is reserved or uncertain; do not repeat.")
    return {"data": result["result"]}


async def report(adapter, execution_id):
    # The journal owns pending callbacks; a restart retries terminal, unreported executions.
    for attempt in range(5):
        result = adapter.state.execution_receipt(execution_id)
        if not result or result["state"] not in {"COMPLETED", "FAILED", "INTERRUPTED", "EXPIRED"}:
            return
        try:
            body = {k: result[k] for k in ("revision", "state", "durationMs", "inputTokens", "outputTokens")}
            await asyncio.to_thread(api.request_json, api_key=adapter.api_key, base_url=adapter.api_base_url,
                method="POST", path="/api/v1/ai-agent/executions/" + quote(execution_id, safe="") + "/completion",
                payload=body)
            adapter.state.execution_reported(execution_id)
            return
        except api.PipefacilAPIError:
            if attempt < 4 and not adapter._closing:
                await asyncio.sleep(min(8, 2 ** attempt))
            else:
                return  # Durable pending callback; CRM also has a single deadline reconciliation.
