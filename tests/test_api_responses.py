"""Exercise compressed API responses without requiring a Hermes gateway."""

import gzip
import importlib.util
import json
from pathlib import Path
import zlib

import httpx
import pytest


spec = importlib.util.spec_from_file_location("api_response_test", Path(__file__).parents[1] / "api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def mock_api(monkeypatch, body, *, encoding="", status=200):
    client_class = httpx.Client
    requests = []

    def respond(request):
        requests.append(request)
        headers = {"Content-Type": "application/json"}
        if encoding:
            headers["Content-Encoding"] = encoding
        return httpx.Response(status, headers=headers, content=body)

    def factory(**kwargs):
        return client_class(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(api.httpx, "Client", factory)
    return requests


@pytest.mark.parametrize("encoding", ["", "gzip", "deflate"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_history_and_upload_responses_are_decoded_once(monkeypatch, encoding, method):
    expected = {"data": {"key": "synthetic-storage-key", "items": [{"text": "Olá"}]}}
    raw = json.dumps(expected, ensure_ascii=False).encode()
    body = gzip.compress(raw) if encoding == "gzip" else zlib.compress(raw) if encoding == "deflate" else raw
    requests = mock_api(monkeypatch, body, encoding=encoding)
    actual = api.request_json(api_key="test-key", base_url="https://crm.example", method=method,
                              path="/fixture", files={"file": ("fixture.txt", b"synthetic")} if method == "POST" else None)
    assert actual == expected
    assert len(requests) == 1  # No retry of an upload whose response failed.
    assert requests[0].method == method
    if method == "POST":
        assert requests[0].headers["Content-Type"].startswith("multipart/form-data;")


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_compressed_api_errors_preserve_http_status(monkeypatch, status):
    mock_api(monkeypatch, gzip.compress(b'{"error":"synthetic"}'), encoding="gzip", status=status)
    with pytest.raises(api.PipefacilAPIError) as exc:
        api.request_json(api_key="test-key", base_url="https://crm.example", method="GET", path="/fixture")
    assert exc.value.status_code == status
    assert "network or timeout" not in str(exc.value)


def test_response_limit_applies_to_decompressed_bytes(monkeypatch):
    mock_api(monkeypatch, gzip.compress(b'{"data":"' + b"x" * (2 * 1024 * 1024) + b'"}'), encoding="gzip")
    with pytest.raises(api.PipefacilAPIError, match="2 MiB"):
        api.request_json(api_key="test-key", base_url="https://crm.example", method="GET", path="/fixture")


def test_compressed_invalid_envelope_is_rejected(monkeypatch):
    mock_api(monkeypatch, gzip.compress(b'{"unexpected":"synthetic"}'), encoding="gzip")
    with pytest.raises(api.PipefacilAPIError, match="invalid API response"):
        api.request_json(api_key="test-key", base_url="https://crm.example", method="GET", path="/fixture")
