"""Authorize fresh CRM facts, validate mutations and enforce the public tool policy."""

import copy
from types import SimpleNamespace
import time

import pytest

from test_hardening import module, kwargs
from test_http_runtime import adapter


@pytest.fixture
def crm(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    client = module("crm").CRM(value, member_user_id="member-user", fields={"notes", "stageId", "customFields", "lostReason"},
                               custom_fields={"interest"}, stages={"qualified", "lost"})
    value.crm = client
    context = {"deal_seq": 7, "contact": {"id": "contact", "phone": "+12025550191"}}
    data = kwargs()
    data["messages"][0]["timestamp"] = time.time()
    context["job_id"] = value.state.admit(data, now=time.time(), max_age=300)
    value.state.next(data["chat_id"])
    lead = {"seq": 7, "contact": dict(context["contact"]), "responsibleUserId": "member-user", "pipelineId": "pipeline",
            "status": "open", "notes": "before", "stageId": "entry", "properties": {"interest": "initial", "private": "canary"}}
    monkeypatch.setattr(module("api"), "get_deal", lambda **k: copy.deepcopy(lead))
    monkeypatch.setattr(module("api"), "get_pipelines", lambda **k: [{"id": "other", "stages": [{"id": "foreign"}]},
        {"id": "pipeline", "stages": [{"id": "qualified", "name": "Qualificado"}, {"id": "lost", "name": "Perdido", "isLost": True}]}])
    yield SimpleNamespace(adapter=value, client=client, context=context, lead=lead)
    value.state.close()


@pytest.mark.parametrize("change", ["contact_id", "contact_phone", "responsible", "seq", "missing_contact"])
def test_crm_cannot_mutate_another_contact_or_reassigned_deal(crm, monkeypatch, change):
    if change == "contact_id":
        crm.lead["contact"]["id"] = "other"
    elif change == "contact_phone":
        crm.lead["contact"]["phone"] = "+12025550199"
    elif change == "responsible":
        crm.lead["responsibleUserId"] = "other"
    elif change == "seq":
        crm.lead["seq"] = 8
    else:
        crm.lead["contact"] = None
    called = []
    monkeypatch.setattr(module("api"), "update_deal", lambda **k: called.append(k))
    with pytest.raises(module("api").PipefacilAPIError):
        crm.client.update(crm.context, "key", {"notes": "new"}, lambda: True)
    assert called == [] and crm.adapter.state.status()["actions"] == {}


def test_closed_deal_chat_is_preserved_but_updates_require_current_member(crm):
    crm.lead["status"], crm.lead["responsibleUserId"] = "won", "other"
    assert crm.client.authorized(crm.context, "key")["status"] == "won"
    with pytest.raises(module("api").PipefacilAPIError, match="assigned"):
        crm.client.authorized(crm.context, "key", writing=True)
    crm.client.member_user_id = ""
    assert crm.client.read(crm.context, "key")["writableFields"] == []
    with pytest.raises(module("api").PipefacilAPIError, match="MEMBER_USER_ID"):
        crm.client.authorized(crm.context, "key", writing=True)


@pytest.mark.parametrize("properties", [{"workspaceId": "other"}, {"notes": "x" * 2001}, {"notes": True},
    {"notes": "x\x00y"}, {"customFields": {"private": "changed"}}, {"customFields": {"interest": {"code": "execute"}}},
    {"customFields": {"interest": float("nan")}}, {"stageId": "foreign"}, {"stageId": "lost"},
    {"contact": {"phone": "+12025550199"}}, {}, {"customFields": {"interest": "x" * 33000}}])
def test_updates_validate_fields_values_and_pipeline_stages_before_any_write(crm, monkeypatch, properties):
    called = []
    monkeypatch.setattr(module("api"), "update_deal", lambda **k: called.append(k))
    with pytest.raises(ValueError):
        crm.client.update(crm.context, "key", properties, lambda: True)
    assert called == [] and crm.adapter.state.status()["actions"] == {}


def test_update_success_requires_authorized_readback_and_is_reused(crm, monkeypatch):
    called = []
    def patch(**k):
        called.append(k)
        p = k["properties"]
        crm.lead["notes"], crm.lead["stageId"] = p["notes"], p["stageId"]
        crm.lead["properties"].update(p["customFields"])
        return {"data": {}}
    monkeypatch.setattr(module("api"), "update_deal", patch)
    properties = {"notes": "confirmed", "stageId": "qualified", "customFields": {"interest": "yes"}}
    assert crm.client.read(crm.context, "key")["customFields"] == {"interest": "initial"}
    result = crm.client.update(crm.context, "key", properties, lambda: True)
    assert result["updated"] is True
    assert crm.client.update(crm.context, "key", properties, lambda: True) == result
    assert len(called) == 1
    assert crm.lead["properties"]["private"] == "canary"


def test_unconfirmed_patch_is_uncertain_and_never_repeated(crm, monkeypatch):
    called = []
    monkeypatch.setattr(module("api"), "update_deal", lambda **k: called.append(k) or {"data": {"success": True}})
    with pytest.raises(module("api").PipefacilAPIError, match="confirmed"):
        crm.client.update(crm.context, "key", {"notes": "new"}, lambda: True)
    with pytest.raises(module("api").PipefacilAPIError, match="uncertain"):
        crm.client.update(crm.context, "key", {"notes": "new"}, lambda: True)
    assert len(called) == 1


def test_revoked_turn_cannot_patch(crm, monkeypatch):
    called = []
    monkeypatch.setattr(module("api"), "update_deal", lambda **k: called.append(k))
    with pytest.raises(module("api").PipefacilAPIError, match="active"):
        crm.client.update(crm.context, "key", {"notes": "new"}, lambda: False)
    assert called == []


@pytest.mark.parametrize("tool", ["terminal", "read_file", "write_file", "browser_navigate", "delegate_task", "cronjob", "memory", "execute_code"])
def test_runtime_policy_blocks_non_public_tools(tool, monkeypatch):
    import gateway.session_context
    monkeypatch.setattr(gateway.session_context, "get_session_env", lambda name: "pipefacil")
    assert module("policy").pre_tool_call(tool)["action"] == "block"


def test_runtime_policy_allows_scoped_tools_and_single_wrapper_and_leaves_operator_context(monkeypatch):
    import gateway.session_context
    policy = module("policy")
    monkeypatch.setattr(gateway.session_context, "get_session_env", lambda name: "pipefacil")
    for name in policy.NAMES | {"tool_search", "tool_describe"}:
        assert policy.pre_tool_call(name) is None
    assert policy.pre_tool_call("tool_call", {"calls": [{"name": "pipefacil_list_media", "arguments": {}}]}) is None
    for calls in [[{"name": "terminal", "arguments": {}}], [{"name": "pipefacil_list_media", "arguments": {}}] * 2,
                  [{"name": "tool_call", "arguments": {}}]]:
        assert policy.pre_tool_call("tool_call", {"calls": calls})["action"] == "block"
    correction = policy.pre_tool_call("tool_call", {"calls": [{"name": "pipefacil_read_profile_file", "arguments": {"path": "knowledge/info.txt"}}, {"name": "pipefacil_list_media", "arguments": {}}]})
    assert "knowledge/info.txt" in correction["message"] and "Execute primeiro" in correction["message"]
    monkeypatch.setattr(gateway.session_context, "get_session_env", lambda name: "cli")
    assert policy.pre_tool_call("terminal") is None


def test_public_response_removes_known_and_detectable_credentials():
    security = module("security")
    result = security.public_text("key-one sk-proj-123456789012345678901234 Bearer 12345678901234567890", ("key-one",))
    assert "key-one" not in result and "sk-proj" not in result and "Bearer" not in result
