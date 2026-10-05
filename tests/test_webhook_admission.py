"""Replay the stale/read-receipt burst without contacting a model or WhatsApp."""
from __future__ import annotations

import asyncio
import contextlib
import json
import hashlib
import hmac
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from test_gateway_sessions import _modules


def _adapter(home):
    module, _ = _modules()
    adapter = object.__new__(module.PipefacilAdapter)
    adapter.profile_home = home
    adapter.max_message_age_seconds = 300
    adapter._runtime_scope = contextlib.nullcontext
    adapter._destinations = {}
    adapter._inbound_tasks = set()
    adapter.webhook_secret = "test-secret"
    adapter.webhook_secret_next = ""
    adapter._closing = False
    adapter._ready_error = None
    adapter._workers = {}
    adapter._slots = asyncio.Semaphore(4)
    adapter.turn_timeout = 30
    adapter._last_maintenance = 0
    adapter.channel_ids = frozenset()
    adapter._counts = {"authenticated": 0, "rejected": 0, "duplicate": 0, "admitted": 0}
    state = __import__(module.__package__ + ".state", fromlist=["State"])
    adapter.state = state.State(home)
    return adapter


def _message(message_id="new", age=0, body="oi"):
    return {"id": message_id, "body": body, "type": "text",
            "timestamp": datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat()}


async def _post(adapter, messages, channel="channel"):
    data = json.dumps({"type": "message.received", "data": {
        "channel": {"id": channel}, "contact": {"phone": "+5511999999999"}, "messages": messages,
    }}).encode()
    class Request:
        content_length = len(data)
        # A new delivery timestamp never makes an old message fresh.
        timestamp = str(int(time.time() * 1000))
        headers = {"X-PipeFacil-Timestamp": timestamp, "X-PipeFacil-Signature-256": "sha256=" + hmac.new(
            adapter.webhook_secret.encode(), timestamp.encode() + b"." + data, hashlib.sha256).hexdigest()}
        async def read(self):
            return data
    response = await adapter._handle_webhook(Request())
    if adapter._inbound_tasks:
        await asyncio.gather(*list(adapter._inbound_tasks), return_exceptions=True)
    return response.status, json.loads(response.body)


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-10-03T12:00:00", True, float("inf"), 0])
def test_missing_invalid_or_ambiguous_message_time_never_dispatches(tmp_path, timestamp):
    adapter = _adapter(tmp_path)
    async def forbidden(**kwargs):
        pytest.fail("invalid event must not enter processing")
    adapter._process_event = forbidden
    message = _message()
    message["timestamp"] = timestamp
    status, result = asyncio.run(_post(adapter, [message]))
    if timestamp == float("inf"):
        assert status == 400 and result["error"] == "invalid_json"
    else:
        assert status == 200 and result["status"] == "ignored"
    assert adapter._destinations == {}
    assert adapter.state.status()["jobs"] == {}


@pytest.mark.parametrize("body,age", [("oi", 3600), ("/reset", 3600), ("oi", -120)])
def test_old_messages_and_old_reset_or_future_events_do_not_run(tmp_path, body, age):
    adapter = _adapter(tmp_path)
    async def forbidden(**kwargs):
        pytest.fail("stale /reset must not rotate the session or send a confirmation")
    adapter._process_event = forbidden
    assert asyncio.run(_post(adapter, [_message(body=body, age=age)]))[1]["status"] == "ignored"
    assert adapter._destinations == {}


def test_27_historical_read_receipts_are_ignored_and_mixed_batch_only_dispatches_new(tmp_path):
    adapter = _adapter(tmp_path)
    received = []
    async def capture(**kwargs):
        received.extend(kwargs["messages"])
    adapter._process_event = capture
    old = [_message(str(index), age=86400, body="/reset" if index == 10 else "oi") for index in range(27)]
    async def scenario():
        assert (await _post(adapter, old))[1]["status"] == "ignored"
        assert received == []
        assert (await _post(adapter, old + [_message("fresh")]))[1]["status"] == "accepted"
        assert [message["id"] for message in received] == ["fresh"]
    asyncio.run(scenario())


def test_receipts_survive_adapter_restart_and_are_scoped_to_profile_and_channel(tmp_path):
    received = []
    message = _message()
    def adapter(home):
        result = _adapter(home)
        async def capture(**kwargs):
            received.append(kwargs["chat_id"])
        result._process_event = capture
        return result
    async def scenario():
        assert (await _post(adapter(tmp_path / "a"), [message]))[1]["status"] == "accepted"
        assert (await _post(adapter(tmp_path / "a"), [message]))[1]["status"] == "duplicate"
        assert (await _post(adapter(tmp_path / "b"), [message]))[1]["status"] == "accepted"
        assert (await _post(adapter(tmp_path / "a"), [message], channel="other"))[1]["status"] == "accepted"
        assert len(received) == 3
    asyncio.run(scenario())


def test_receipt_database_failure_refuses_admission(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.state.path.unlink()
    adapter.state.path.mkdir()
    async def forbidden(**kwargs):
        pytest.fail("unrecorded messages must not be dispatched")
    adapter._process_event = forbidden
    status, result = asyncio.run(_post(adapter, [_message()]))
    assert status == 503 and "storage unavailable" in result["error"]
    assert adapter._destinations == {}


def test_claims_are_atomic_across_concurrent_workers_and_store_no_contact_or_body(tmp_path):
    inbox = __import__(_modules()[0].__package__ + ".inbox", fromlist=["*"])
    message = _message(body="confidential test body")
    def claim(_):
        return inbox.claim_messages(tmp_path, "channel:+5511999999999", [message], now=time.time())
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(len(claimed) for claimed in executor.map(claim, range(16))) == 1
    with sqlite3.connect(tmp_path / "pipefacil-state" / "inbox.sqlite3") as db:
        assert len(db.execute("SELECT * FROM receipts").fetchall()) == 1
    persisted = (tmp_path / "pipefacil-state" / "inbox.sqlite3").read_bytes()
    assert b"5511999999999" not in persisted and b"confidential test body" not in persisted


def test_processing_failure_does_not_allow_replay_of_an_ambiguous_delivery(tmp_path):
    adapter = _adapter(tmp_path)
    calls = []
    async def failure(**kwargs):
        calls.append("attempt")
        raise RuntimeError("outcome cannot be confirmed")
    adapter._process_event = failure
    message = _message()
    async def scenario():
        assert (await _post(adapter, [message]))[1]["status"] == "accepted"
        assert (await _post(adapter, [message]))[1]["status"] == "duplicate"
        assert calls == ["attempt"]
    asyncio.run(scenario())


def test_freshness_boundaries_and_numeric_times():
    inbox = __import__(_modules()[0].__package__ + ".inbox", fromlist=["*"])
    now = 1_800_000_000
    values = [now - 300, now - 301, (now + 30) * 1000, (now + 31) * 1000]
    messages = [{"timestamp": value} for value in values]
    assert inbox.fresh_messages(messages, now=now, max_age=300) == [messages[0], messages[2]]
