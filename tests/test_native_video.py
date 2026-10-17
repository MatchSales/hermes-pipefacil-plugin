"""Approved native videos keep media isolation and execution-scoped delivery."""
import httpx
import pytest
from test_hardening import module

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42"


def test_video_catalog_and_tool_never_treat_it_as_a_document(tmp_path):
    library, tools = module("library"), module("tools")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "presentation.mp4").write_bytes(MP4)
    asset = library.catalog(tmp_path)[0]
    assert (asset.kind, asset.mime) == ("video", "video/mp4")
    assert tools._prepare_outbound_message({"type": "video", "fileId": asset.id}, tmp_path)["type"] == "video"
    with pytest.raises(ValueError):
        tools._prepare_outbound_message({"type": "document", "fileId": asset.id}, tmp_path)
    foreign = tmp_path / "other"
    foreign.mkdir()
    with pytest.raises(ValueError):
        library.read_asset(foreign, asset.id, "video")


def test_remote_video_uses_exact_profile_url_and_valid_mp4_bytes(tmp_path, monkeypatch):
    library, tools = module("library"), module("tools")
    url = "https://media.example/presentation.mp4"
    (tmp_path / "SOUL.md").write_text(f"- label: Apresentação | type: video | url: {url}\n")
    message = tools._prepare_outbound_message({"type": "video", "url": url}, tmp_path)
    assert message == {"type": "video", "mediaLink": url, "mimeType": "video/mp4"}
    real_client = httpx.Client
    monkeypatch.setattr(library.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=MP4, headers={"Content-Type": "video/mp4"}))))
    asset, body, _ = library.read_remote_asset(tmp_path, url, "video")
    assert asset.mime == "video/mp4" and body == MP4
    with pytest.raises(ValueError):
        tools._prepare_outbound_message({"type": "video", "url": url + "?other=1"}, tmp_path)
    with pytest.raises(ValueError):
        library.read_remote_asset(tmp_path, url, "document")


@pytest.mark.parametrize("body", [b"<html>not a video</html>", b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00isommp42", b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00isommp42", b"\x00\x00\x00\x04ftypisom"])
def test_forged_mp4_is_rejected_before_upload(tmp_path, body):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "fake.mp4").write_bytes(body)
    library = module("library")
    asset = library.catalog(tmp_path)[0]
    with pytest.raises(ValueError, match="invalid_media_content"):
        library.read_asset(tmp_path, asset.id, "video")


def test_video_api_payload_remains_compatible_with_external_profiles(monkeypatch):
    api = module("api")
    calls = []
    monkeypatch.setattr(api, "request_json", lambda **kwargs: calls.append(kwargs) or {"data": {"id": "sent"}})
    api.send_message(api_key="test-key", base_url="https://crm.example", recipient="+5585000000000", message_type="video", media_link="https://media.example/media/approved", caption="Apresentação", mime_type="video/mp4", channel_id="original-channel")
    assert calls[0]["payload"] == {"to": "+5585000000000", "channelId": "original-channel", "type": "video", "mediaLink": "https://media.example/media/approved", "mimeType": "video/mp4", "caption": "Apresentação"}


@pytest.mark.parametrize("pause_during_upload", [False, True])
def test_managed_video_stays_on_execution_channel_and_rechecks_after_upload(tmp_path, monkeypatch, pause_during_upload):
    import asyncio
    import hashlib

    from aiohttp import web
    from test_gateway_sessions import _modules
    from test_http_runtime import drain, listener
    from test_managed_execution import command, managed_adapter

    value = managed_adapter(tmp_path, monkeypatch)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "approved.mp4").write_bytes(MP4)
    asset = module("library").catalog(tmp_path)[0]
    original = command("native-video")
    execution = original["payload"]["execution"]
    receipts, sent, uploads = {}, [], []
    paused = False

    def upload(**kwargs):
        nonlocal paused
        uploads.append(kwargs)
        paused = pause_during_upload
        return {"key": "a" * 64, "url": "https://media.example/media/" + "a" * 64,
                "filename": "approved.mp4", "contentType": "video/mp4", "sizeBytes": len(MP4),
                "sha256": hashlib.sha256(MP4).hexdigest()}

    monkeypatch.setattr(_modules()[0], "upload_message_media", upload)

    async def crm(request):
        if "/receipts/" in request.path:
            receipt = receipts.get(request.path.rsplit("/", 1)[-1])
            return web.json_response({"data": receipt}, status=200 if receipt else 404)
        assert "/executions/native-video" in request.path
        if request.path.endswith("/completion"):
            return web.json_response({"data": {"ok": True}})
        if paused:
            return web.json_response({"error": "paused"}, status=409)
        if request.method == "GET":
            if request.path.endswith("/messages"):
                return web.json_response({"data": []})
            return web.json_response({"data": {"execution": execution, "deal": {
                "id": execution["dealId"], "seq": 1, "status": "open", "responsibleUserId": "human-a",
                "contact": original["payload"]["data"]["contact"]}}})
        body = await request.json()
        assert "to" not in body and "channelId" not in body
        sent.append(body)
        receipt = {"status": "APPLIED", "result": {"id": "sent-" + str(len(sent)), "status": "sent"}}
        receipts[request.headers["Idempotency-Key"]] = receipt
        return web.json_response({"data": receipt})

    async def scenario():
        async def handler(event):
            result = await value.send_api_message(event.source.chat_id, {"type": "video", "fileId": asset.id})
            assert result["message_id"]
            return "Segue o vídeo solicitado."
        value.set_message_handler(handler)
        app = web.Application()
        app.router.add_route("*", "/api/v1/ai-agent/{tail:.*}", crm)
        server, value.api_base_url = await listener(app)
        try:
            assert (await value.accept_managed_execution(original))["state"] == "ACCEPTED"
            await asyncio.wait_for(drain(value), 6)
            if pause_during_upload:
                assert sent == []
            else:
                assert [body["type"] for body in sent] == ["video", "text"]
                assert sent[0]["mimeType"] == "video/mp4"
                assert sent[0]["mediaUrl"] == "https://media.example/media/" + "a" * 64
            assert len(uploads) == 1
        finally:
            value._closing = True
            await value.disconnect()
            await server.cleanup()
    asyncio.run(scenario())
