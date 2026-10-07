"""Persistent chat media, profile isolation, legacy cache migration and safe failure."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from test_hardening import module, kwargs
from test_http_runtime import adapter


def current_turn(value, identity):
    data = kwargs(identity)
    data["messages"][0]["timestamp"] = time.time()
    job = value.state.admit(data, now=time.time(), max_age=300)
    value.state.next(data["chat_id"])
    return {"job_id": job, "destination": {"phone": data["phone"], "channel_id": "channel"}}


@pytest.mark.parametrize("profile", range(8))
@pytest.mark.parametrize("kind,name,body,mime", [
    ("image", "photo.jpeg", b"\xff\xd8\xffpixels", "image/jpeg"),
    ("document", "catalog.pdf", b"%PDF-1.7\ncontents", "application/pdf"),
])
def test_every_profile_sends_asset_id_and_reuses_it_across_turns(tmp_path, monkeypatch, profile, kind, name, body, mime):
    value = adapter(tmp_path / f"sdr-{profile}", monkeypatch)
    value.crm = SimpleNamespace(member_user_id="")
    home = value.profile_home
    (home / "media").mkdir()
    (home / "media" / name).write_bytes(body)
    item = module("library").catalog(home)[0]
    context = current_turn(value, "first")
    monkeypatch.setattr(value, "_live_turn_context", lambda chat: context)
    uploads, posts = [], []
    def request(**kw):
        if kw["path"] == "/api/v1/conversations/media":
            uploads.append(kw)
            return {"data": {"assetId": f"asset-{profile}", "filename": name, "contentType": mime}}
        assert kw["path"] == "/api/v1/conversations/messages"
        posts.append(kw["payload"])
        return {"data": {"id": f"message-{len(posts)}", "status": "sent"}}
    monkeypatch.setattr(module("api"), "request_json", request)
    monkeypatch.setattr(module("adapter"), "media_url", lambda **kw: pytest.fail("Temporary URL must not be used"))
    try:
        message = {"type": kind, "fileId": item.id, "caption": "Segue o arquivo"}
        asyncio.run(value.send_api_message("chat", message))
        value.state.finish(context["job_id"], "completed")
        context = current_turn(value, "second")
        asyncio.run(value.send_api_message("chat", message))
        assert len(uploads) == 1 and len(posts) == 2
        assert uploads[0]["files"]["file"] == (name, body, mime)
        assert all(p["mediaAssetId"] == f"asset-{profile}" and "mediaLink" not in p for p in posts)
        assert all(p["filename"] == name and p["mimeType"] == mime and p["caption"] == "Segue o arquivo" for p in posts)
    finally:
        value.state.close()


@pytest.mark.parametrize("status", [404, 405, 403, 503, None])
def test_failed_upload_never_posts_a_message_or_falls_back(tmp_path, monkeypatch, status):
    value = adapter(tmp_path, monkeypatch)
    value.crm = SimpleNamespace(member_user_id="")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "photo.jpg").write_bytes(b"\xff\xd8\xffpixels")
    context = current_turn(value, "first")
    monkeypatch.setattr(value, "_live_turn_context", lambda chat: context)
    calls = []
    def request(**kw):
        calls.append(kw["path"])
        raise module("api").PipefacilAPIError("upload failed", status_code=status)
    monkeypatch.setattr(module("api"), "request_json", request)
    try:
        with pytest.raises(module("api").PipefacilAPIError):
            asyncio.run(value.send_api_message("chat", {"type": "image", "fileId": module("library").catalog(tmp_path)[0].id}))
        assert calls == ["/api/v1/conversations/media"]
        if status is None or status >= 500:
            assert value.state.status()["actions"]["uncertain"] == 1
    finally:
        value.state.close()


def test_asset_send_omits_temporary_url_and_rejects_ambiguous_reference(monkeypatch):
    api = module("api")
    calls = []
    monkeypatch.setattr(api, "request_json", lambda **kw: calls.append(kw) or {"data": {"id": "message"}})
    args = dict(api_key="key", base_url="https://crm.example", recipient="+12025550191", message_type="image")
    api.send_message(**args, media_asset_id="asset-1", caption="Caption")
    assert calls[0]["payload"] == {"to": "+12025550191", "type": "image", "mediaAssetId": "asset-1", "caption": "Caption"}
    with pytest.raises(api.PipefacilAPIError):
        api.send_message(**args, media_asset_id="asset-1", media_link="https://example.org/photo.jpg")
    assert len(calls) == 1


@pytest.mark.parametrize("receipt", [{"key": "legacy-file"}, {"assetId": ""}, {"assetId": 1}, []])
def test_legacy_or_invalid_receipt_is_not_a_persistent_asset(monkeypatch, receipt):
    api = module("api")
    monkeypatch.setattr(api, "request_json", lambda **kw: {"data": receipt})
    with pytest.raises(api.PipefacilAPIError, match="invalid persistent"):
        api.upload_message_media(api_key="key", base_url="https://crm.example", filename="photo.jpg",
                                mime_type="image/jpeg", body=b"bytes")


def test_remote_media_is_copied_with_bounds_and_profile_allowlist(tmp_path, monkeypatch):
    library = module("library")
    url = "https://files.example.org/photo.jpg?temporary-signature=example"
    tmp_path.joinpath("SOUL.md").write_text(f"- label: Foto | type: image | url: {url}\n")
    calls = []
    def client(**options):
        assert options["follow_redirects"] is False and options["trust_env"] is False
        assert isinstance(options["transport"], module("network").PublicTransport)
        def respond(request):
            calls.append(request)
            return httpx.Response(200, content=b"\xff\xd8\xffpixels", headers={"Content-Type": "image/jpeg"})
        return real_client(transport=httpx.MockTransport(respond))
    real_client = httpx.Client
    monkeypatch.setattr(library.httpx, "Client", client)
    asset, body, digest = library.read_remote_asset(tmp_path, url, "image")
    assert asset.filename == "photo.jpg" and asset.mime == "image/jpeg" and body.startswith(b"\xff\xd8\xff") and digest
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="media_not_in_profile"):
        library.read_remote_asset(other, url, "image")
    assert len(calls) == 1


def test_legacy_cache_is_excluded_and_credentials_and_api_origins_are_isolated(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    value.crm = SimpleNamespace(member_user_id="")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "photo.jpg").write_bytes(b"\xff\xd8\xffpixels")
    item = module("library").catalog(tmp_path)[0]
    _, _, content_hash = module("library").read_asset(tmp_path, item.id, "image")
    import hashlib
    legacy_key = module("state").digest([str(value.profile_home), value.api_base_url,
                                          hashlib.sha256(b"key-1").hexdigest(), content_hash])
    value.state.upload(legacy_key, {"key": "legacy-custom-field-file"})
    credential = ["key-1"]
    monkeypatch.setattr(module("adapter"), "get_scoped_secret", lambda name, default="": credential[0])
    context = current_turn(value, "first")
    monkeypatch.setattr(value, "_live_turn_context", lambda chat: context)
    uploads, sent = [], []
    def request(**kw):
        if kw["path"] == "/api/v1/conversations/media":
            uploads.append((kw["api_key"], kw["base_url"]))
            return {"data": {"assetId": f"asset-{len(uploads)}"}}
        sent.append(kw["payload"])
        return {"data": {"id": f"message-{len(sent)}"}}
    monkeypatch.setattr(module("api"), "request_json", request)
    origin = value.api_base_url
    try:
        for number, (key, base) in enumerate([
            ("key-1", origin), ("key-2", origin), ("key-1", "https://other-api.example"), ("key-1", origin),
        ]):
            credential[0], value.api_base_url = key, base
            asyncio.run(value.send_api_message("chat", {"type": "image", "fileId": item.id}))
            value.state.finish(context["job_id"], "completed")
            context = current_turn(value, f"next-{number}")
        assert len(uploads) == 3
        assert [m["mediaAssetId"] for m in sent] == ["asset-1", "asset-2", "asset-3", "asset-1"]
        assert value.state.upload(legacy_key) == {"key": "legacy-custom-field-file"}
    finally:
        value.state.close()


@pytest.mark.parametrize("headers,body", [
    ({"Content-Type": "image/jpeg", "Content-Length": str(16 * 1024 * 1024 + 1)}, b"\xff\xd8\xffpixels"),
    ({"Content-Type": "text/html"}, b"<html>not an image</html>"),
    ({"Content-Type": "image/jpeg"}, b"<html>spoofed image</html>"),
])
def test_approved_url_still_requires_valid_bounded_bytes(tmp_path, monkeypatch, headers, body):
    library = module("library")
    url = "https://files.example.org/photo.jpg"
    tmp_path.joinpath("SOUL.md").write_text(f"- label: Foto | type: image | url: {url}\n")
    real_client = httpx.Client
    monkeypatch.setattr(library.httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body, headers=headers))))
    with pytest.raises(ValueError, match="invalid_media"):
        library.read_remote_asset(tmp_path, url, "image")
