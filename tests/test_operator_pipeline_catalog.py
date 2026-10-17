from types import SimpleNamespace

import pytest
from test_hardening import module


CATALOG = [{"id": "sales", "stages": [
    {"id": "entry", "name": "Entrada"}, {"id": "loss", "name": "Perda", "isLost": True},
    {"id": "not-allowed"}]}]


def test_operator_catalog_only_applies_when_managed_api_omits_catalog(monkeypatch):
    api, managed = module("api"), module("managed")
    token = managed.CURRENT.set({"execution": {}})
    try:
        monkeypatch.setattr(managed, "context", lambda **kwargs: {"deal": {}})
        client = module("crm").CRM(SimpleNamespace(api_base_url="https://crm.example"),
            member_user_id="bot", fields=[], custom_fields=[], stages=["entry", "loss"],
            pipeline_catalog=CATALOG)
        assert [s["id"] for s in client.allowed_stages({"pipelineId": "sales"}, "own-key")] == ["entry", "loss"]
        assert client.allowed_stages({"pipelineId": "another"}, "own-key") == []
        monkeypatch.setattr(managed, "context", lambda **kwargs: {"pipelines": []})
        assert client.allowed_stages({"pipelineId": "sales"}, "own-key") == []
        def unavailable(**kwargs):
            raise api.PipefacilAPIError("Authorization failed", status_code=403)
        monkeypatch.setattr(managed, "context", unavailable)
        with pytest.raises(api.PipefacilAPIError):
            client.allowed_stages({"pipelineId": "sales"}, "own-key")
    finally:
        managed.CURRENT.reset(token)


def test_external_legacy_keeps_live_catalog_even_with_operator_snapshot(monkeypatch):
    api = module("api")
    calls = []
    monkeypatch.setattr(api, "request_json", lambda **kwargs: calls.append(kwargs) or {"data": []})
    assert api.get_pipelines(api_key="key", base_url="https://crm.example", operator_catalog=CATALOG) == []
    assert calls[0]["path"] == "/api/v1/pipelines"


@pytest.mark.parametrize("catalog", [
    [{"id": "sales", "stages": [{"id": "stage", "isLost": "false"}]}],
    [{"id": "sales", "stages": [{"id": "same"}, {"id": "same"}]}],
    [{"id": "sales", "stages": []}, {"id": "sales", "stages": []}],
])
def test_malformed_operator_catalog_fails_closed(catalog):
    with pytest.raises(ValueError):
        module("crm").CRM(SimpleNamespace(), member_user_id="bot", fields=[],
            custom_fields=[], stages=[], pipeline_catalog=catalog)
