import copy

import pytest
from test_hardening import module
from test_managed_execution import command


def test_private_file_uses_execution_multipart_and_durable_receipt_not_public_media(monkeypatch):
    api, managed = module("api"), module("managed")
    requests, receipts = [], {}
    def request(**kw):
        requests.append(kw)
        if kw["method"] == "GET":
            key = kw["path"].rsplit("/", 1)[-1]
            if key not in receipts:
                raise api.PipefacilAPIError("Absent", status_code=404)
            return {"data": receipts[key]}
        assert kw["path"] == "/api/v1/ai-agent/executions/file-execution/files/conta_de_energia"
        assert kw["files"] == {"file": ("conta.pdf", b"%PDF-1.7 synthetic", "application/pdf")}
        receipt = {"status": "APPLIED", "result": {"attached": True, "field": "conta_de_energia",
                   "file": {"key": "custom-fields/own-workspace/synthetic.pdf"}}}
        receipts[kw["idempotency_key"]] = receipt
        return {"data": receipt}
    monkeypatch.setattr(api, "request_json", request)
    token = managed.CURRENT.set({"execution": command("file-execution")["payload"]["execution"]})
    try:
        args = dict(api_key="own-key", base_url="https://crm.example", field_slug="conta_de_energia",
                    filename="conta.pdf", mime_type="application/pdf", body=b"%PDF-1.7 synthetic")
        assert api.attach_deal_file(**args)["attached"] is True
        assert api.attach_deal_file(**args)["attached"] is True
        assert sum(r["method"] == "POST" for r in requests) == 1
        assert all("/custom-fields/upload" not in r["path"] for r in requests)
    finally:
        managed.CURRENT.reset(token)


def test_analytical_note_is_idempotent_across_turns_but_not_across_deals(monkeypatch):
    api, managed = module("api"), module("managed")
    receipts, posts = {}, []
    def request(**kw):
        if kw["method"] == "GET":
            key = kw["path"].rsplit("/", 1)[-1]
            if key not in receipts:
                raise api.PipefacilAPIError("Absent", status_code=404)
            return {"data": receipts[key]}
        posts.append(kw)
        receipts[kw["idempotency_key"]] = {"status": "APPLIED", "result": {"id": "note"}}
        return {"data": receipts[kw["idempotency_key"]]}
    monkeypatch.setattr(api, "request_json", request)
    for identity, deal in [("first", "one-deal"), ("second", "one-deal"), ("third", "other-deal")]:
        token = managed.CURRENT.set({"execution": command(identity, deal=deal)["payload"]["execution"]})
        try:
            api.create_deal_note(api_key="own-key", base_url="https://crm.example", content="Synthetic analysis")
        finally:
            managed.CURRENT.reset(token)
    assert len(posts) == 2
    assert all(p["payload"] == {"content": "Synthetic analysis"} for p in posts)


def test_uncertain_private_file_does_not_repeat_post(monkeypatch):
    api, managed = module("api"), module("managed")
    posts, reserved = [], False
    def request(**kw):
        nonlocal reserved
        if kw["method"] == "GET":
            if not reserved:
                raise api.PipefacilAPIError("Absent", status_code=404)
            return {"data": {"status": "UNCERTAIN", "result": None}}
        posts.append(kw)
        reserved = True
        raise api.PipefacilAPIError("Lost upload response")
    monkeypatch.setattr(api, "request_json", request)
    token = managed.CURRENT.set({"execution": command("uncertain-file")["payload"]["execution"]})
    try:
        for _ in range(2):
            with pytest.raises(api.PipefacilAPIError, match="uncertain"):
                api.attach_deal_file(api_key="own-key", base_url="https://crm.example",
                    field_slug="conta_de_energia", filename="conta.pdf", mime_type="application/pdf", body=b"%PDF-1.7")
        assert len(posts) == 1
    finally:
        managed.CURRENT.reset(token)


def test_managed_terminal_stage_uses_atomic_receipt_without_get_after_loss(tmp_path, monkeypatch):
    api, managed, crm = module("api"), module("managed"), module("crm")
    from types import SimpleNamespace
    lead = {"id": "deal-a", "seq": 1, "pipelineId": "sales", "status": "open",
            "contact": {"id": "contact-a", "phone": "+5585999991111"}}
    adapter = SimpleNamespace(api_base_url="https://crm.example", state=SimpleNamespace(handoff=lambda job: None),
                              effect=lambda job, kind, properties, write, **kwargs: write())
    client = crm.CRM(adapter, member_user_id="bot", fields=["stageId", "lostReason"], custom_fields=[], stages=["loss"])
    calls = []
    monkeypatch.setattr(api, "get_deal", lambda **kw: calls.append("GET") or copy.deepcopy(lead))
    monkeypatch.setattr(api, "get_pipelines", lambda **kw: [{"id": "sales", "stages": [{"id": "loss", "isLost": True}]}])
    monkeypatch.setattr(api, "update_deal", lambda **kw: {"data": {"id": "deal-a", "seq": 1, "contactId": "contact-a"}})
    token = managed.CURRENT.set({"execution": command()["payload"]["execution"]})
    try:
        result = client.update({"job_id": 1, "deal_seq": 1, "contact": lead["contact"]}, "own-key",
                               {"stageId": "loss", "lostReason": "Outro"}, lambda: True)
        assert result["success"] is True and calls == ["GET"]
    finally:
        managed.CURRENT.reset(token)
