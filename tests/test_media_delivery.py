"""Behavior checks for Pipefacil media contracts and current-webhook attachment flow."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _plugin_package():
    package_name = "pipefacil_plugin_test"
    existing = sys.modules.get(package_name)
    if existing is not None:
        return package_name
    spec = importlib.util.spec_from_file_location(
        package_name,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    return package_name


def test_profile_allowlist_api_payload_order_and_partial_result(tmp_path, monkeypatch):
    package = _plugin_package()
    media = __import__(f"{package}.media", fromlist=["*"])
    api = __import__(f"{package}.api", fromlist=["*"])
    tools = __import__(f"{package}.tools", fromlist=["*"])
    adapter_module = __import__(f"{package}.adapter", fromlist=["*"])

    profile_a = tmp_path / "profile-a"
    profile_b = tmp_path / "profile-b"
    profile_a.mkdir()
    profile_b.mkdir()
    profile_a.joinpath(".env").write_text("PIPEFACIL_API_KEY=key-a\n", encoding="utf-8")
    profile_b.joinpath(".env").write_text("PIPEFACIL_API_KEY=key-b\n", encoding="utf-8")
    url_a = "https://assets.example.org/a/catalog.pdf?signature=a1"
    url_b = "https://assets.example.org/b/photo.png?signature=b1"
    profile_a.joinpath("SOUL.md").write_text(
        f"- label: Catálogo | type: document | url: {url_a}\n", encoding="utf-8"
    )
    profile_b.joinpath("SOUL.md").write_text(
        f"- label: Foto | type: image | url: {url_b}\n", encoding="utf-8"
    )

    assert media.resolve_media_link(profile_a, kind="document", url=url_a) is not None
    assert media.resolve_media_link(profile_b, kind="image", url=url_b) is not None
    assert media.resolve_media_link(profile_a, kind="image", url=url_b) is None
    assert media.resolve_media_link(profile_a, kind="document", url="http://assets.example.org/a.pdf") is None
    assert media.resolve_media_link(profile_a, kind="document", url=url_a) is not None

    from gateway.platforms._shared import get_scoped_secret
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override

    for profile, expected in ((profile_a, "key-a"), (profile_b, "key-b"), (profile_a, "key-a")):
        home_token = set_hermes_home_override(str(profile))
        secret_token = set_secret_scope(build_profile_secret_scope(profile), profile_home=str(profile))
        try:
            assert get_hermes_home().resolve() == profile.resolve()
            assert get_scoped_secret("PIPEFACIL_API_KEY") == expected
        finally:
            reset_secret_scope(secret_token)
            reset_hermes_home_override(home_token)

    api_calls = []
    monkeypatch.setattr(api, "request_json", lambda **kwargs: api_calls.append(kwargs) or {"data": {}})
    api.send_message(
        api_key="key", base_url="https://pipefacil.example.org", recipient="+5511999999999",
        message_type="text", text="Olá", channel_id="channel-1",
    )
    api.send_message(
        api_key="key", base_url="https://pipefacil.example.org", recipient="+5511999999999",
        message_type="image", media_link="https://assets.example.org/team.jpg?sig=x",
        caption="Nossa equipe", mime_type="image/jpeg", channel_id="channel-1",
    )
    api.send_message(
        api_key="key", base_url="https://pipefacil.example.org", recipient="+5511999999999",
        message_type="document", media_link=url_a, caption="Catálogo", filename="catalog.pdf",
        mime_type="application/pdf", sender_phone_number_id="phone-1",
    )
    assert [call["payload"] for call in api_calls] == [
        {"to": "+5511999999999", "type": "text", "text": "Olá", "channelId": "channel-1"},
        {
            "to": "+5511999999999", "type": "image", "mediaLink": "https://assets.example.org/team.jpg?sig=x",
            "caption": "Nossa equipe", "mimeType": "image/jpeg", "channelId": "channel-1",
        },
        {
            "to": "+5511999999999", "type": "document", "mediaLink": url_a, "caption": "Catálogo",
            "filename": "catalog.pdf", "mimeType": "application/pdf", "senderPhoneNumberId": "phone-1",
        },
    ]

    prepared_doc = tools._prepare_outbound_message(
        {"type": "document", "url": url_a, "caption": "Catálogo"}, profile_a
    )
    assert prepared_doc == {
        "type": "document", "mediaLink": url_a, "caption": "Catálogo",
        "filename": "catalog.pdf", "mimeType": "application/pdf",
    }
    with pytest.raises(ValueError, match="not listed"):
        tools._prepare_outbound_message({"type": "image", "url": url_b}, profile_a)

    class ToolCapture:
        registrations = {}

        def register_tool(self, **registration):
            self.registrations[registration["name"]] = registration

    tool_capture = ToolCapture()
    tools.register_tools(tool_capture)
    send_schema = tool_capture.registrations["pipefacil_send_messages"]["schema"]["parameters"]
    assert set(send_schema["properties"]) == {"messages"}
    assert send_schema["properties"]["messages"]["maxItems"] == 2

    home_module = __import__("hermes_constants")
    monkeypatch.setattr(home_module, "get_hermes_home", lambda: profile_a)
    monkeypatch.setattr(tools, "_active_pipefacil_chat", lambda session_id, home: ("channel-1:+5511999999999", ""))

    class FakeAdapter:
        def __init__(self):
            self.sent = []
            self.fail_on = None

        async def send_api_message(self, chat_id, message):
            self.sent.append((chat_id, dict(message)))
            if self.fail_on == len(self.sent):
                from pipefacil_plugin_test.api import PipefacilAPIError

                raise PipefacilAPIError("Pipefacil returned HTTP 503.", status_code=503)
            return {"message_id": f"id-{len(self.sent)}", "status": "accepted"}

    fake = FakeAdapter()
    monkeypatch.setattr(adapter_module, "adapter_for_profile", lambda home, chat_id: fake)
    from tools.approval_context import reset_current_observability_context, set_current_observability_context

    turn_tokens = set_current_observability_context(turn_id="turn-a", session_id="session-a")
    try:
        result = asyncio.run(tools._send_messages(
            {"messages": [
                {"type": "text", "text": "Parte inicial"},
                {"type": "document", "url": url_a, "caption": "Segue o catálogo"},
            ]},
            session_id="session-a",
        ))
        assert [message["type"] for _, message in fake.sent] == ["text", "document"]
        assert "accepted_by_api" in result
        assert "delivery_confirmed" in result
        too_many = asyncio.run(tools._send_messages(
            {"messages": [{"type": "text", "text": "Uma terceira mensagem"}]},
            session_id="session-a",
        ))
        assert "at most two preliminary messages" in too_many
    finally:
        reset_current_observability_context(turn_tokens)

    fake.sent.clear()
    fake.fail_on = 2
    turn_tokens = set_current_observability_context(turn_id="turn-partial", session_id="session-a")
    try:
        partial = asyncio.run(tools._send_messages(
            {"messages": [
                {"type": "text", "text": "Primeira parte"},
                {"type": "text", "text": "Segunda parte"},
            ]},
            session_id="session-a",
        ))
    finally:
        reset_current_observability_context(turn_tokens)
    assert "Partial result" in partial
    assert "accepted 1 of 2" in partial
    assert "Do not claim the failed message was sent" in partial


class _FakeResponse:
    def __init__(self, status: int, mime: str, body: bytes):
        self.status_code = status
        self.headers = {"Content-Type": mime, "Content-Length": str(len(body))}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, chunk_size):
        yield self._body


class _FakeHTTPClient:
    response = None

    def __init__(self, **kwargs):
        self.options = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, url, headers=None):
        assert method == "GET"
        assert url.startswith("https://")
        assert self.options["follow_redirects"] is False
        return self.response


@pytest.mark.parametrize(
    ("kind", "mime", "filename", "body"),
    [
        ("image", "image/jpeg", "lead.jpg", b"\xff\xd8\xff" + b"pixels"),
        ("document", "application/pdf", "document.pdf", b"%PDF-1.7\ncontents"),
    ],
)
def test_current_webhook_media_reaches_native_hermes_event_and_failures_are_explicit(
    tmp_path, monkeypatch, kind, mime, filename, body,
):
    package = _plugin_package()
    media = __import__(f"{package}.media", fromlist=["*"])
    adapter_module = __import__(f"{package}.adapter", fromlist=["*"])
    captured = []

    class FakeSource:
        user_id = "lead-1"
        user_name = "Lead"
        message_id = None
        profile = None

    adapter = object.__new__(adapter_module.PipefacilAdapter)
    adapter.profile_home = tmp_path
    adapter.api_base_url = "https://pipefacil.example.org"
    adapter.history_limit = 10
    adapter._hermes_profile_name = None
    adapter._turn_context_lock = threading.RLock()
    adapter._active_turn_context = {}
    adapter._history_reset_marker = lambda chat_id: None
    adapter.build_source = lambda **kwargs: FakeSource()

    async def capture_event(event):
        captured.append(event)

    adapter.handle_message = capture_event
    monkeypatch.setattr(adapter_module, "get_scoped_secret", lambda *args: "profile-key")
    monkeypatch.setattr(adapter_module, "fetch_conversation_history", lambda **kwargs: ([], False))
    monkeypatch.setattr(media.httpx, "Client", _FakeHTTPClient)
    _FakeHTTPClient.response = _FakeResponse(200, mime, body)

    message = {
        "id": "current-1",
        "type": kind,
        "body": "Veja este arquivo",
        "media": {
            "downloadUrl": "https://files.example.org/temporary-link",
            "mimeType": mime,
            "filename": filename,
        },
    }
    asyncio.run(adapter._process_event(
        payload={"data": {}}, messages=[message], contact={"name": "Lead", "phone": "+5511999999999"},
        channel={}, chat_id="channel-1:+5511999999999", phone="+5511999999999",
    ))
    event = captured[-1]
    assert len(event.media_urls) == 1
    assert event.media_types == [mime]
    assert Path(event.media_urls[0]).read_bytes() == body
    assert "Anexo atual recebido" in event.text

    _FakeHTTPClient.response = _FakeResponse(403, "application/json", b"expired")
    failed_message = {**message, "id": "current-expired"}
    asyncio.run(adapter._process_event(
        payload={"data": {}}, messages=[failed_message], contact={"name": "Lead", "phone": "+5511999999999"},
        channel={}, chat_id="channel-1:+5511999999999", phone="+5511999999999",
    ))
    failed_event = captured[-1]
    assert failed_event.media_urls == []
    assert "temporary link may have expired" in failed_event.text
    assert "Não afirme que analisou" in failed_event.text
