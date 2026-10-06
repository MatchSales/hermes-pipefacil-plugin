"""Transfer last, confirm without GET access, and never retry an ambiguous assignment."""

import asyncio
import copy

import pytest

from test_crm_policy import crm as crm
from test_hardening import module
from test_guidance import definitions


@pytest.fixture
def flow(crm, monkeypatch):
    crm.client.handoff_user_id = "human-user"
    crm.context["destination"] = {"phone": crm.context["contact"]["phone"]}
    calls = []
    transferred = False

    def get(**kwargs):
        calls.append(("get", None))
        if transferred:
            pytest.fail("Handoff must not GET the lead after assignment revokes access")
        return copy.deepcopy(crm.lead)

    def patch(**kwargs):
        nonlocal transferred
        properties = copy.deepcopy(kwargs["properties"])
        calls.append(("patch", copy.deepcopy(properties)))
        if "customFields" in properties:
            crm.lead["properties"].update(properties.pop("customFields"))
        crm.lead.update(properties)
        transferred = "responsibleUserId" in properties
        return {"data": {**copy.deepcopy(crm.lead), "contactId": crm.lead["contact"]["id"]}}

    def send(**kwargs):
        calls.append(("send", kwargs["text"]))
        return {"data": {"id": "closing-message", "status": "accepted"}}

    monkeypatch.setattr(module("api"), "get_deal", get)
    monkeypatch.setattr(module("api"), "update_deal", patch)
    monkeypatch.setattr(module("adapter"), "send_message", send)
    monkeypatch.setattr(crm.adapter, "_live_turn_context", lambda chat: crm.context)
    crm.calls, crm.patch = calls, patch
    return crm


def run(flow, args=None, active=lambda: True):
    return asyncio.run(module("handoff").execute(flow.adapter, "chat", flow.context, "key", args or {}, active))


def test_fields_readback_closing_text_and_assignment_in_that_order(flow):
    properties = {"notes": "complete", "customFields": {"interest": "yes"}, "stageId": "qualified"}
    args = {"properties": properties, "message": "Vou encaminhar seu atendimento."}
    result = run(flow, args)
    first = flow.calls.index(("patch", properties))
    notice = flow.calls.index(("send", args["message"]))
    assert any(kind == "get" for kind, _ in flow.calls[first + 1:notice])
    assert notice > first
    assert flow.calls[-1] == ("patch", {"responsibleUserId": "human-user"})
    assert result["handed_off"] and result["terminal"] and result["stageId"] == "qualified"
    assert flow.lead["properties"]["private"] == "canary"
    before = list(flow.calls)
    assert run(flow, args) == result  # Saved receipt, despite inaccessible lead.
    assert flow.calls == before
    assert flow.adapter.state.handoff(flow.context["job_id"])["result"] == result


def test_transfer_without_pending_fields_or_closing_text(flow):
    assert run(flow)["handed_off"]
    assert [c for c in flow.calls if c[0] != "get"] == [("patch", {"responsibleUserId": "human-user"})]


@pytest.mark.parametrize("args", [
    {"responsibleUserId": "attacker"}, {"seq": 9}, {"phone": "+12025550199"},
    {"properties": {"responsibleUserId": "attacker"}}, {"properties": {"workspaceId": "other"}},
    {"properties": {"customFields": {"private": "change"}}}, {"properties": []},
    {"message": ""}, {"message": "x" * 4001}, {"message": "x\x00y"},
    {"properties": {"stageId": "lost", "lostReason": "no"}}, {"properties": {"stageId": "foreign"}},
])
def test_invalid_handoff_does_not_write_or_send(flow, args):
    with pytest.raises(ValueError):
        run(flow, args)
    assert all(kind == "get" for kind, _ in flow.calls)
    assert flow.adapter.state.status()["actions"] == {}


@pytest.mark.parametrize("target", ["", "member-user"])
def test_handoff_requires_operator_configured_different_responsible(flow, target):
    flow.client.handoff_user_id = target
    with pytest.raises(ValueError, match="HANDOFF_USER_ID"):
        run(flow)
    assert flow.calls == []


def test_no_transfer_or_closing_notice_when_fields_are_unconfirmed(flow, monkeypatch):
    monkeypatch.setattr(module("api"), "update_deal", lambda **kw: {"data": {}})
    with pytest.raises(module("api").PipefacilAPIError, match="confirmed"):
        run(flow, {"properties": {"notes": "changed"}, "message": "Vou encaminhar."})
    assert not any(kind == "send" for kind, _ in flow.calls)
    assert flow.adapter.state.status()["actions"] == {"uncertain": 1}
    with pytest.raises(module("api").PipefacilAPIError, match="prior_action_uncertain"):
        run(flow, {"message": "Vou encaminhar."})
    assert flow.adapter.state.handoff(flow.context["job_id"]) is None


def test_patch_succeeded_but_403_readback_is_uncertain_and_blocks_transfer(flow, monkeypatch):
    def patch(**kwargs):
        def denied(**kw):
            raise module("api").PipefacilAPIError("no access", status_code=403)
        monkeypatch.setattr(module("api"), "get_deal", denied)
        return {"data": {}}
    monkeypatch.setattr(module("api"), "update_deal", patch)
    with pytest.raises(module("api").PipefacilAPIError, match="readback is uncertain"):
        run(flow, {"properties": {"notes": "changed"}})
    assert flow.adapter.state.status()["actions"] == {"uncertain": 1}
    with pytest.raises(module("api").PipefacilAPIError, match="prior_action_uncertain"):
        run(flow)


@pytest.mark.parametrize("field", ["seq", "contactId", "pipelineId", "stageId", "responsibleUserId", "status"])
def test_invalid_transfer_receipt_is_uncertain_and_terminal(flow, monkeypatch, field):
    def patch(**kwargs):
        envelope = flow.patch(**kwargs)
        envelope["data"][field] = "wrong"
        return envelope
    monkeypatch.setattr(module("api"), "update_deal", patch)
    with pytest.raises(module("api").PipefacilAPIError, match="receipt is uncertain"):
        run(flow)
    assert flow.adapter.state.handoff(flow.context["job_id"])["state"] == "uncertain"
    with pytest.raises(module("api").PipefacilAPIError, match="already attempted"):
        run(flow, {"message": "different arguments must not bypass retry protection"})
    assert [k for k, _ in flow.calls].count("patch") == 1
    assert asyncio.run(flow.adapter.send("chat", "Não repita", metadata={"notify": True})).success


@pytest.mark.parametrize("status", [None, 404, 503])
def test_transfer_transport_or_http_failure_is_not_repeated(flow, monkeypatch, status):
    attempts = []
    def patch(**kwargs):
        attempts.append(kwargs)
        raise module("api").PipefacilAPIError("failed", status_code=status)
    monkeypatch.setattr(module("api"), "update_deal", patch)
    with pytest.raises(module("api").PipefacilAPIError, match="failed"):
        run(flow)
    with pytest.raises(module("api").PipefacilAPIError, match="already attempted"):
        run(flow)
    assert len(attempts) == 1
    assert flow.adapter.state.handoff(flow.context["job_id"])["state"] == "uncertain"


def test_handoff_blocks_all_subsequent_crm_operations_and_messages(flow):
    run(flow)
    before = list(flow.calls)
    with pytest.raises(module("api").PipefacilAPIError, match="terminal"):
        flow.client.read(flow.context, "key")
    with pytest.raises(module("api").PipefacilAPIError, match="terminal"):
        flow.client.update(flow.context, "key", {"notes": "too late"}, lambda: True)
    with pytest.raises(module("api").PipefacilAPIError, match="terminal"):
        asyncio.run(flow.adapter.send_api_message("chat", {"type": "text", "text": "too late"}))
    for kind in ["send", "upload", "update_deal", "handoff"]:
        with pytest.raises(module("state").StateError, match="terminal"):
            flow.adapter.state.claim_action(flow.context["job_id"], kind, {"changed": True})
    assert asyncio.run(flow.adapter.send("chat", "automatic final", metadata={"notify": True})).success
    assert flow.calls == before


def test_closing_message_failure_prevents_transfer(flow, monkeypatch):
    def send(**kwargs):
        raise module("api").PipefacilAPIError("send outcome uncertain")
    monkeypatch.setattr(module("adapter"), "send_message", send)
    with pytest.raises(module("api").PipefacilAPIError, match="send outcome uncertain"):
        run(flow, {"message": "Vou encaminhar."})
    assert not any(kind == "patch" for kind, _ in flow.calls)
    with pytest.raises(module("api").PipefacilAPIError, match="prior_action_uncertain"):
        run(flow)


def test_owner_or_fields_changing_during_notice_prevents_transfer(flow, monkeypatch):
    def send(**kwargs):
        flow.lead["responsibleUserId"] = "someone-else"
        return {"data": {"id": "notice"}}
    monkeypatch.setattr(module("adapter"), "send_message", send)
    with pytest.raises(module("api").PipefacilAPIError, match="assigned"):
        run(flow, {"message": "Vou encaminhar."})
    assert not any(kind == "patch" for kind, _ in flow.calls)


def test_crash_with_pending_handoff_remains_terminal_after_recovery(flow):
    flow.adapter.state.claim_action(flow.context["job_id"], "handoff", {"target": "human-user"})
    flow.adapter.state.close()
    flow.adapter.state = module("state").State(flow.adapter.profile_home)
    flow.adapter.state.acquire()
    flow.adapter.state.recover()
    assert flow.adapter.state.handoff(flow.context["job_id"])["state"] == "uncertain"
    with pytest.raises(module("api").PipefacilAPIError, match="already attempted"):
        run(flow)
    assert flow.calls == []


def test_confirmed_handoff_receipt_survives_reopening_the_journal(flow):
    result = run(flow)
    flow.adapter.state.close()
    flow.adapter.state = module("state").State(flow.adapter.profile_home)
    flow.adapter.state.acquire()
    before = list(flow.calls)
    assert run(flow) == result
    assert flow.calls == before


def test_revoked_turn_cannot_transfer_or_send(flow):
    with pytest.raises(module("api").PipefacilAPIError, match="no longer active"):
        run(flow, {"message": "Vou encaminhar."}, active=lambda: False)
    assert flow.calls == []


def test_handoff_schema_and_routing_do_not_accept_model_selected_identity(monkeypatch):
    tool = next(t for t in definitions() if t["name"] == "pipefacil_handoff")
    parameters = tool["schema"]["parameters"]
    assert set(parameters["properties"]) == {"properties", "message"}
    assert "responsibleUserId" not in parameters["properties"]["properties"]["properties"]
    monkeypatch.setattr(module("tools"), "_active_pipefacil_chat", lambda *args: (None, "profile route rejected"))
    assert "profile route rejected" in asyncio.run(tool["handler"]({}, session_id="other-profile"))
