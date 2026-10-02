"""Behavior checks for reset commands delivered in Pipefacil webhook batches."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import sys
import threading
from dataclasses import make_dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _reset_module():
    package_name = "pipefacil_reset_test"
    spec = importlib.util.spec_from_file_location(
        package_name,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    return __import__(f"{package_name}.reset", fromlist=["*"])


def test_reset_in_a_batch_starts_at_last_inbound_command_and_keeps_later_lead_messages():
    reset = _reset_module()
    messages = [
        {"type": "text", "body": "old context"},
        {"type": "text", "body": "\u200e/reset\u200e"},
        {"type": "text", "body": "/reset", "fromMe": True},
        {"type": "text", "body": "oi"},
        {"type": "text", "body": "mensagem da equipe", "fromMe": True},
    ]

    reset_index = reset._reset_command_index(messages)

    assert reset_index == 1
    assert reset._messages_after_reset(messages, reset_index) == [messages[3]]


def test_history_after_reset_uses_message_order_when_timestamps_match():
    reset = _reset_module()
    same_time = "2026-10-01T20:00:00Z"
    history = [
        {"id": "before", "body": "old", "timestamp": same_time},
        {"id": "reset", "body": "/reset", "timestamp": same_time},
        {"id": "after", "body": "new", "timestamp": same_time},
    ]

    assert reset._history_after_reset(history, (1790884800.0, "reset")) == [history[2]]


def _adapter_with_fake_gateway(monkeypatch):
    """Load the plugin's reset path without requiring a Hermes installation."""
    gateway = ModuleType("gateway")
    gateway.__path__ = []
    platforms = ModuleType("gateway.platforms")
    platforms.__path__ = []
    config = ModuleType("gateway.config")
    config.Platform = str
    config.PlatformConfig = object
    shared = ModuleType("gateway.platforms._shared")
    shared.get_scoped_secret = lambda *args: "test-key"
    base = ModuleType("gateway.platforms.base")
    base.BasePlatformAdapter = object
    base.SendResult = object
    event = ModuleType("gateway.platforms.event")
    event.MessageEvent = lambda **kwargs: SimpleNamespace(**kwargs)
    event.MessageType = SimpleNamespace(TEXT="text", PHOTO="photo", DOCUMENT="document")
    for name, module in (
        ("gateway", gateway), ("gateway.platforms", platforms), ("gateway.config", config),
        ("gateway.platforms._shared", shared), ("gateway.platforms.base", base),
        ("gateway.platforms.event", event),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    package_name = "pipefacil_reset_flow_test"
    spec = importlib.util.spec_from_file_location(
        package_name, PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, package_name, package)
    spec.loader.exec_module(package)
    return __import__(f"{package_name}.adapter", fromlist=["*"])


def test_message_event_supports_hermes_with_and_without_reply_expected(monkeypatch):
    adapter_module = _adapter_with_fake_gateway(monkeypatch)
    legacy_event = make_dataclass("LegacyMessageEvent", [("text", str)])
    monkeypatch.setattr(adapter_module, "MessageEvent", legacy_event)
    assert adapter_module._new_message_event(text="oi", reply_expected=True).text == "oi"

    current_event = make_dataclass("CurrentMessageEvent", [("text", str), ("reply_expected", bool)])
    monkeypatch.setattr(adapter_module, "MessageEvent", current_event)
    assert adapter_module._new_message_event(text="oi", reply_expected=True).reply_expected is True


@pytest.mark.parametrize("headers", [
    {},
    {"X-Pipefacil-Timestamp": "0", "X-Pipefacil-Signature-256": "sha256=invalid"},
])
def test_webhook_reaches_payload_validation_without_signature(monkeypatch, headers):
    adapter_module = _adapter_with_fake_gateway(monkeypatch)
    aiohttp = ModuleType("aiohttp")
    aiohttp.web = SimpleNamespace(
        json_response=lambda payload, status=200: SimpleNamespace(payload=payload, status=status),
    )
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)
    monkeypatch.setattr(
        adapter_module, "get_scoped_secret",
        lambda name, default="": "api-key" if name == "PIPEFACIL_API_KEY" else "",
    )
    assert adapter_module._credentials_present() is True

    adapter = object.__new__(adapter_module.PipefacilAdapter)
    adapter._runtime_scope = contextlib.nullcontext
    body = json.dumps({"type": "message.received", "data": {}}).encode()
    request_headers = headers

    class Request:
        content_length = len(body)
        headers = request_headers

        async def read(self):
            return body

    response = asyncio.run(adapter._handle_webhook(Request()))
    assert response.status == 422
    assert response.payload == {"error": "contact phone is required"}


@pytest.mark.parametrize("outcome", ["ok", "gateway_error", "purge_error"])
def test_reset_confirmation_follows_session_cleanup_and_history_boundary(tmp_path, monkeypatch, outcome):
    adapter_module = _adapter_with_fake_gateway(monkeypatch)
    adapter = object.__new__(adapter_module.PipefacilAdapter)
    adapter.profile_home = tmp_path
    adapter._history_reset_lock = threading.RLock()
    adapter._pending_reset_purges = {"old-session": "pipefacil:lead"}
    adapter._reset_purge_results = {}
    adapter.reset_allowed_users = {"5511999999999"}
    adapter._seen_message_ids = {}
    adapter._runtime_scope = contextlib.nullcontext
    adapter._build_contact_source = lambda **kwargs: SimpleNamespace(
        user_id="lead", user_name="Lead", message_id=kwargs["message_id"],
    )
    adapter._mark_session_for_reset_purge = lambda source: "old-session"
    calls = []

    async def reset_session(event):
        calls.append(("reset", event.allow_gateway_control))
        if outcome == "gateway_error":
            raise RuntimeError("gateway failed")
        if outcome == "ok":
            adapter._reset_purge_results["old-session"] = True

    async def send(chat_id, text):
        calls.append(("send", text))

    def record_boundary(*args):
        calls.append(("boundary", args))
        return True

    adapter.gateway_runner = SimpleNamespace(_handle_reset_command=reset_session)
    adapter.send = send
    adapter._record_history_reset = record_boundary
    adapter.handle_message = lambda event: pytest.fail("/reset must not enter the confirmation prompt")
    adapter.purge_reset_session = lambda *args, **kwargs: False

    asyncio.run(adapter._process_event(
        payload={"data": {}},
        messages=[{"id": "reset-1", "type": "text", "body": "/reset",
                   "timestamp": "2026-10-01T20:00:00Z"}],
        contact={"phone": "+5511999999999"}, channel={},
        chat_id="channel:+5511999999999", phone="+5511999999999",
    ))

    if outcome != "ok":
        assert [name for name, _ in calls] == ["reset", "send"]
        assert "não consegui" in calls[-1][1].lower()
    else:
        assert [name for name, _ in calls] == ["reset", "boundary", "send"]
        assert calls[0][1] is True
        assert "apagado" in calls[-1][1]


def test_reset_from_public_lead_is_rejected_without_rotating_session(tmp_path, monkeypatch):
    adapter_module = _adapter_with_fake_gateway(monkeypatch)
    adapter = object.__new__(adapter_module.PipefacilAdapter)
    adapter.reset_allowed_users = set()
    sent = []

    async def send(chat_id, text):
        sent.append(text)

    adapter.send = send
    adapter._mark_session_for_reset_purge = lambda source: pytest.fail("public reset must be denied")
    asyncio.run(adapter._process_event(
        payload={"data": {}},
        messages=[{"id": "reset-1", "type": "text", "body": "/reset"}],
        contact={"phone": "+5511999999999"}, channel={},
        chat_id="channel:+5511999999999", phone="+5511999999999",
    ))
    assert sent == ["Comando não disponível neste atendimento."]


@pytest.mark.parametrize("delete_fails", [False, True])
def test_reset_purge_reports_whether_the_old_transcript_was_deleted(tmp_path, monkeypatch, delete_fails):
    adapter_module = _adapter_with_fake_gateway(monkeypatch)
    adapter = object.__new__(adapter_module.PipefacilAdapter)
    adapter._history_reset_lock = threading.RLock()
    adapter._pending_reset_purges = {"old-session": "pipefacil:lead"}
    adapter._reset_purge_results = {}
    removed = []

    def delete_session(session_id, **kwargs):
        if delete_fails:
            raise RuntimeError("active write guard")
        return True

    store = SimpleNamespace(
        _db_for_key=lambda key: SimpleNamespace(delete_session=delete_session),
        remove_by_session_id=removed.append,
    )
    adapter.gateway_runner = SimpleNamespace(session_store=store)

    assert adapter.purge_reset_session("old-session", sessions_dir=tmp_path) is True
    if delete_fails:
        assert adapter._reset_purge_results == {}
        assert adapter._pending_reset_purges == {"old-session": "pipefacil:lead"}
        assert removed == []
    else:
        assert adapter._reset_purge_results == {"old-session": True}
        assert adapter._pending_reset_purges == {}
        assert removed == ["old-session"]
