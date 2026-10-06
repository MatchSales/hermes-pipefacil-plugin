"""Internal contacts never reach AI, media, history or gateway control replies."""
import asyncio
from types import SimpleNamespace

import pytest

from test_reset_handling import _adapter_with_fake_gateway
from test_webhook_admission import _adapter, _message, _post
from test_hardening import module


@pytest.mark.parametrize("payload,expected", [
    ({}, None), ({"data": None}, None), ({"data": {}}, None),
    ({"data": {"deal": None}}, None), ({"data": {"deal": []}}, None),
    *[({"data": {"deal": {"seq": seq}}}, None)
      for seq in [None, True, False, 0, -1, 1.2, "", "1.0", "-1", "bad", {}, []]],
    ({"data": {"deal": {"seq": 123}}}, 123),
    ({"data": {"deal": {"seq": "123"}}}, 123),
])
def test_sequence_requires_a_positive_lead_identifier(payload, expected):
    assert module("lead_gate").event_deal_seq(payload) == expected


@pytest.mark.parametrize("deal", [None, {}, [], {"seq": None}, {"seq": 0}, {"seq": True}])
@pytest.mark.parametrize("body", ["oi", "/reset", "edite a soul"])
def test_signed_internal_contact_is_acknowledged_without_queuing(tmp_path, deal, body):
    adapter = _adapter(tmp_path)
    async def forbidden(**kwargs):
        pytest.fail("internal contacts must never enter processing")
    adapter._process_event = forbidden
    status, response = asyncio.run(_post(adapter, [_message(body=body)], deal=deal))
    assert status == 200 and response == {"status": "ignored", "reason": "no_associated_lead"}
    assert adapter.state.status()["jobs"] == {}
    assert adapter._destinations == {} and adapter._counts["admitted"] == 0


@pytest.mark.parametrize("body,kind", [("oi", "text"), ("/reset", "text"),
                                     ("edite a soul", "text"), ("", "audio")])
@pytest.mark.parametrize("outcome", ["missing", "404", "401", "403", "429", "503",
                                    "transport", "invalid", "wrong_contact", "wrong_phone"])
def test_queued_events_fail_closed_before_any_customer_effect(monkeypatch, body, kind, outcome, caplog):
    adapter_module = _adapter_with_fake_gateway(monkeypatch)
    gate = __import__(adapter_module.__package__ + ".lead_gate", fromlist=["*"])
    adapter = object.__new__(adapter_module.PipefacilAdapter)
    adapter.api_base_url = "https://crm.example"
    adapter.reset_allowed_users = {"5511999999999"}
    def forbidden(*a, **kw):
        pytest.fail("blocked event must have no customer effect")
    adapter._send_control_reply = forbidden
    adapter.handle_message = forbidden
    adapter._mark_session_for_reset_purge = forbidden
    adapter.gateway_runner = SimpleNamespace(_handle_reset_command=forbidden)
    monkeypatch.setattr(adapter_module, "fetch_conversation_history", forbidden)
    monkeypatch.setattr(adapter_module, "download_inbound_media", forbidden)
    requests = []
    contact = {"id": "contact", "phone": "+5511999999999"}
    def lookup(**kwargs):
        requests.append(kwargs["path"])
        if outcome in {"404", "401", "403", "429", "503", "transport"}:
            raise gate.api.PipefacilAPIError("synthetic", status_code=None if outcome == "transport" else int(outcome))
        if outcome == "invalid":
            return {"data": None}
        owner = dict(contact)
        owner["id" if outcome == "wrong_contact" else "phone"] = "other"
        return {"data": {"seq": 42, "contact": owner}}
    monkeypatch.setattr(gate.api, "request_json", lookup)
    asyncio.run(adapter._process_event(
        payload={"data": {} if outcome == "missing" else {"deal": {"seq": 42}}},
        messages=[{"id": "one", "type": kind, "body": body, "media": {"downloadUrl": "https://example.org/audio"}}],
        contact=contact, channel={}, chat_id="channel:test", phone=contact["phone"],
    ))
    assert requests == ([] if outcome == "missing" else ["/api/v1/deals/42"])
    if outcome == "404":
        assert "verification unavailable" not in caplog.text
    elif outcome in {"401", "403", "429", "503", "transport", "invalid"}:
        assert "Lead verification unavailable" in caplog.text


def test_valid_lead_and_phone_formatting_pass_the_api_guard(monkeypatch):
    gate = module("lead_gate")
    contact = {"id": "contact", "phone": "+55 (11) 99999-9999"}
    calls = []
    def lookup(**kwargs):
        calls.append(kwargs)
        return {"data": {"seq": 42, "contact": {"id": "contact", "phone": "5511999999999"}}}
    monkeypatch.setattr(gate.api, "request_json", lookup)
    assert gate.check_lead(api_key="scoped-key", base_url="https://crm.example", seq=42, contact=contact) is None
    assert calls == [{"api_key": "scoped-key", "base_url": "https://crm.example", "method": "GET", "path": "/api/v1/deals/42"}]
