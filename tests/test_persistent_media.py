"""Persistent chat media, profile isolation, legacy cache migration and safe failure."""

import asyncio
import time
import hashlib
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
def test_every_profile_sends_stable_r2_url_and_reuses_it_across_turns(tmp_path, monkeypatch, profile, kind, name, body, mime):
    value = adapter(tmp_path / f"sdr-{profile}", monkeypatch)
    value.crm = SimpleNamespace(member_user_id="")
    home = value.profile_home
    (home / "media").mkdir()
    (home / "media" / name).write_bytes(body)
    item = module("library").catalog(home)[0]
    context = current_turn(value, "first")
    monkeypatch.setattr(value, "_live_turn_context", lambda chat: context)
    uploads, posts = [], []
    def upload(**kw):
        uploads.append(kw)
        return {"key": "a" * 64, "url": "https://media.example/media/" + "a" * 64,
                "filename": name, "contentType": mime, "sizeBytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    def request(**kw):
        assert kw["path"] == "/api/v1/conversations/messages"
        posts.append(kw["payload"])
        return {"data": {"id": f"message-{len(posts)}", "status": "sent"}}
    monkeypatch.setattr(module("adapter"), "upload_message_media", upload)
    monkeypatch.setattr(module("api"), "request_json", request)
    monkeypatch.setattr(module("adapter"), "media_url", lambda **kw: pytest.fail("Temporary URL must not be used"))
    try:
        message = {"type": kind, "fileId": item.id, "caption": "Segue o arquivo"}
        asyncio.run(value.send_api_message("chat", message))
        value.state.finish(context["job_id"], "completed")
        context = current_turn(value, "second")
        asyncio.run(value.send_api_message("chat", message))
        assert len(uploads) == 1 and len(posts) == 2
        assert (uploads[0]["filename"], uploads[0]["body"], uploads[0]["mime_type"]) == (name, body, mime)
        assert uploads[0]["token"] == "test-media-token"
        assert all(p["mediaLink"] == "https://media.example/media/" + "a" * 64 and "mediaAssetId" not in p for p in posts)
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
    def upload(**kw):
        calls.append("upload")
        raise module("api").PipefacilAPIError("upload failed", status_code=status)
    monkeypatch.setattr(module("adapter"), "upload_message_media", upload)
    monkeypatch.setattr(module("api"), "request_json", lambda **kw: pytest.fail("No CRM POST after failed upload"))
    try:
        with pytest.raises(module("api").PipefacilAPIError):
            asyncio.run(value.send_api_message("chat", {"type": "image", "fileId": module("library").catalog(tmp_path)[0].id}))
        assert calls == ["upload"]
        if status is None or status >= 500:
            assert value.state.status()["actions"]["uncertain"] == 1
    finally:
        value.state.close()


@pytest.mark.parametrize("change", ["key", "url", "filename", "contentType", "sizeBytes", "sha256"])
def test_invalid_or_expiring_receipts_are_rejected(change):
    storage = module("storage")
    body = b"bytes"
    receipt = {"key": "a" * 64, "url": "https://media.example/media/" + "a" * 64,
               "filename": "photo.jpg", "contentType": "image/jpeg", "sizeBytes": len(body),
               "sha256": hashlib.sha256(body).hexdigest()}
    receipt[change] = "wrong"
    with pytest.raises(module("api").PipefacilAPIError, match="invalid persistent"):
        storage.validate_receipt(receipt, base_url="https://media.example", filename="photo.jpg",
                                 mime_type="image/jpeg", body=body)


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
    legacy_key = module("state").digest([str(value.profile_home), value.api_base_url,
                                          hashlib.sha256(b"key-1").hexdigest(), content_hash])
    value.state.upload(legacy_key, {"key": "legacy-custom-field-file"})
    credential = ["key-1"]
    monkeypatch.setattr(module("adapter"), "get_scoped_secret", lambda name, default="": credential[0] if name == "PIPEFACIL_API_KEY" else "test-media-token" if name == "PIPEFACIL_MEDIA_UPLOAD_TOKEN" else default)
    context = current_turn(value, "first")
    monkeypatch.setattr(value, "_live_turn_context", lambda chat: context)
    uploads, sent = [], []
    def upload(**kw):
        uploads.append((credential[0], value.api_base_url))
        return {"key": str(len(uploads)) * 64, "url": "https://media.example/media/" + str(len(uploads)) * 64,
                "filename": "photo.jpg", "contentType": "image/jpeg", "sizeBytes": len(kw["body"]),
                "sha256": hashlib.sha256(kw["body"]).hexdigest()}
    def request(**kw):
        sent.append(kw["payload"])
        return {"data": {"id": f"message-{len(sent)}"}}
    monkeypatch.setattr(module("adapter"), "upload_message_media", upload)
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
        assert [m["mediaLink"].rsplit("/", 1)[1] for m in sent] == ["1" * 64, "2" * 64, "3" * 64, "1" * 64]
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


@pytest.mark.parametrize("base,token", [("", "token"), ("https://media.example", "")])
def test_missing_storage_configuration_never_calls_network(monkeypatch, base, token):
    storage = module("storage")
    monkeypatch.setattr(storage.httpx, "Client", lambda **kw: pytest.fail("Storage config must be checked first"))
    with pytest.raises(module("api").PipefacilAPIError, match="Configure PIPEFACIL_MEDIA"):
        storage.upload_message_media(token=token, base_url=base, filename="photo.jpg", mime_type="image/jpeg", body=b"bytes")


@pytest.mark.parametrize("failure", ["redirect", "huge_receipt", "echoed_secret", "temporary_url"])
def test_storage_receipt_is_bounded_and_cannot_redirect_or_leak_credentials(monkeypatch, failure):
    storage = module("storage")
    token = "private-profile-storage-token"
    body = b"\xff\xd8\xffpixels"
    real_client = httpx.Client
    def respond(request):
        assert str(request.url) == "https://media.example/upload"
        assert request.headers["Authorization"] == "Bearer " + token
        assert request.content == body
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.example"})
        if failure == "huge_receipt":
            return httpx.Response(200, content=b"x" * (64 * 1024 + 1))
        if failure == "echoed_secret":
            return httpx.Response(403, json={"error": token})
        receipt = {"key": "a" * 64, "url": "https://media.example/media/" + "a" * 64 + "?expires=3600",
                   "filename": "photo.jpg", "contentType": "image/jpeg", "sizeBytes": len(body),
                   "sha256": hashlib.sha256(body).hexdigest()}
        return httpx.Response(200, json={"data": receipt})
    def client(**options):
        assert options["follow_redirects"] is False and options["trust_env"] is False
        assert isinstance(options["transport"], module("network").PublicTransport)
        return real_client(transport=httpx.MockTransport(respond), follow_redirects=False)
    monkeypatch.setattr(storage.httpx, "Client", client)
    with pytest.raises(module("api").PipefacilAPIError) as caught:
        storage.upload_message_media(token=token, base_url="https://media.example", filename="photo.jpg",
                                     mime_type="image/jpeg", body=body)
    assert token not in str(caught.value)


def test_media_token_and_origin_rotation_cannot_reuse_old_receipts(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    value.crm = SimpleNamespace(member_user_id="")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "photo.jpg").write_bytes(b"\xff\xd8\xffpixels")
    item = module("library").catalog(tmp_path)[0]
    context = current_turn(value, "first")
    monkeypatch.setattr(value, "_live_turn_context", lambda chat: context)
    token = ["storage-first"]
    monkeypatch.setattr(module("adapter"), "get_scoped_secret", lambda name, default="":
        token[0] if name == "PIPEFACIL_MEDIA_UPLOAD_TOKEN" else "crm-key" if name == "PIPEFACIL_API_KEY" else default)
    uploads, posts = [], []
    def upload(**kw):
        uploads.append((kw["token"], kw["base_url"]))
        return {"key": str(len(uploads)) * 64, "url": kw["base_url"] + "/media/" + str(len(uploads)) * 64,
                "filename": kw["filename"], "contentType": kw["mime_type"], "sizeBytes": len(kw["body"]),
                "sha256": hashlib.sha256(kw["body"]).hexdigest()}
    def request(**kw):
        posts.append(kw["payload"])
        return {"data": {"id": str(len(posts))}}
    monkeypatch.setattr(module("adapter"), "upload_message_media", upload)
    monkeypatch.setattr(module("api"), "request_json", request)
    try:
        for index, (credential, origin) in enumerate([
            ("storage-first", "https://media.example"), ("storage-second", "https://media.example"),
            ("storage-first", "https://other-media.example"), ("storage-first", "https://media.example"),
        ]):
            token[0], value.media_base_url = credential, origin
            asyncio.run(value.send_api_message("chat", {"type": "image", "fileId": item.id}))
            value.state.finish(context["job_id"], "completed")
            context = current_turn(value, f"next-{index}")
        assert len(uploads) == 3
        assert posts[0]["mediaLink"] == posts[3]["mediaLink"]
        assert len({post["mediaLink"] for post in posts}) == 3
    finally:
        value.state.close()


def test_upload_token_is_redacted_from_public_text_and_captions(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    value.crm = SimpleNamespace(member_user_id="")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "photo.jpg").write_bytes(b"\xff\xd8\xffpixels")
    item = module("library").catalog(tmp_path)[0]
    context = current_turn(value, "first")
    monkeypatch.setattr(value, "_live_turn_context", lambda chat: context)
    posts = []
    def upload(**kw):
        return {"key": "a" * 64, "url": "https://media.example/media/" + "a" * 64,
                "filename": kw["filename"], "contentType": kw["mime_type"], "sizeBytes": len(kw["body"]),
                "sha256": hashlib.sha256(kw["body"]).hexdigest()}
    def request(**kw):
        posts.append(kw["payload"])
        return {"data": {"id": str(len(posts))}}
    monkeypatch.setattr(module("adapter"), "upload_message_media", upload)
    monkeypatch.setattr(module("api"), "request_json", request)
    try:
        for message in [{"type": "text", "text": "test-media-token"},
                        {"type": "image", "fileId": item.id, "caption": "test-media-token"}]:
            asyncio.run(value.send_api_message("chat", message))
        assert posts[0]["text"] == posts[1]["caption"] == "[credencial removida]"
        assert all("test-media-token" not in str(post) for post in posts)
    finally:
        value.state.close()
