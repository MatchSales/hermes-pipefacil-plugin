"""Real sockets, gzip, native Hermes background lifecycle and multipart uploads."""

import asyncio
import gzip
import json
import time
from types import SimpleNamespace

from aiohttp import ClientSession, web
import pytest

from test_hardening import module, signed
from test_gateway_sessions import _modules


def adapter(home, monkeypatch, *, capacity=500, concurrency=4, timeout=30):
    from gateway.config import Platform, PlatformConfig
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    home.mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text("PIPEFACIL_API_KEY=test-key\nPIPEFACIL_WEBHOOK_SECRET=literal-secret\n")
    Platform._add_pseudo_member("pipefacil")
    token = set_hermes_home_override(str(home))
    try:
        from gateway.run import _profile_runtime_scope
        with _profile_runtime_scope(home):
            value = _modules()[0].PipefacilAdapter(PlatformConfig(enabled=True, extra={
                "queue_capacity": capacity, "queue_per_chat": min(capacity, 50), "concurrency": concurrency,
                "turn_timeout_seconds": max(timeout, 10)}))
    finally:
        reset_hermes_home_override(token)
    value.turn_timeout = timeout
    value._closing = False
    value.state.acquire()
    monkeypatch.setattr(value, "_acquire_platform_lock", lambda *a: True)
    monkeypatch.setattr(value, "_release_platform_lock", lambda: None)
    monkeypatch.setattr(_modules()[0], "fetch_conversation_history", lambda **k: ([], False))
    async def no_obligation(*args):
        return None
    monkeypatch.setattr(value, "_record_delivery_obligation", no_obligation)
    return value


async def listener(app):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])


def payload(identity="one", *, phone="+12025550191", body="oi"):
    return json.dumps({"type": "message.received", "data": {"channel": {"id": "channel"},
        "contact": {"id": "contact", "phone": phone}, "messages": [{"id": identity, "type": "text", "body": body,
            "timestamp": int(time.time() * 1000)}]}}, ensure_ascii=False).encode()


async def drain(value):
    while value._inbound_tasks:
        await asyncio.gather(*list(value._inbound_tasks))


@pytest.mark.parametrize("shared", [False, True])
def test_real_http_gzip_authentication_on_standalone_and_hermes_shared_listener(tmp_path, monkeypatch, shared):
    from gateway.platforms.shared_ingress import dispatch_profile_ingress, publish_shared_ingress
    value = adapter(tmp_path, monkeypatch)
    received = []
    async def capture(**kwargs):
        received.append(kwargs["messages"])
    value._process_event = capture

    async def scenario():
        child = web.Application(client_max_size=4194304, handler_args={"auto_decompress": False})
        child.router.add_post(value.webhook_path, value._handle_webhook)
        child.router.add_get(value.webhook_path + "/health", value._handle_health)
        app, path = child, value.webhook_path
        if shared:
            runner = SimpleNamespace(_profile_adapters={"sdr-a": {"pipefacil": value}}, adapters={})
            value.gateway_runner = runner
            value._shared_listener_profile = "sdr-a"
            publish_shared_ingress(value, child, value.webhook_path)
            app = web.Application(client_max_size=4194304)  # Hermes root automatically decompresses.
            async def dispatch(request):
                return await dispatch_profile_ingress(runner, request.match_info["profile"], request.match_info["tail"], request, scoped=True)
            app.router.add_route("*", "/p/{profile}/{tail:.*}", dispatch)
            path = "/p/sdr-a" + path
        server, base = await listener(app)
        try:
            async with ClientSession() as client:
                body = payload(body="Olá 🎙️ " + "x" * 1200000)
                headers = {**signed(body), "Content-Encoding": "gzip", "Content-Type": "application/json"}
                async with client.post(base + path, data=gzip.compress(body), headers=headers) as response:
                    assert response.status == 200, await response.text()
                    assert (await response.json())["status"] == "accepted"
                await drain(value)
                assert len(received) == 1
                async with client.post(base + path, data=gzip.compress(body), headers=headers) as response:
                    assert (await response.json())["status"] == "duplicate"
                async with client.post(base + path, data=gzip.compress(body), headers={"Content-Encoding": "gzip"}) as response:
                    assert response.status == 401
                wrong = {**signed(body, "profile-b-secret"), "Content-Encoding": "gzip"}
                async with client.post(base + path, data=gzip.compress(body), headers=wrong) as response:
                    assert response.status == 401
                if shared:
                    async with client.post(base + "/p/unknown" + value.webhook_path, data=body, headers=signed(body)) as response:
                        assert response.status == 404
        finally:
            await server.cleanup()
            await value.disconnect()
    asyncio.run(scenario())


def test_native_hermes_completes_before_next_history_or_model_turn_and_other_chats_run(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch, concurrency=2)
    sequence = []
    first_started = None
    release = None

    async def handler(event):
        sequence.append("start:" + event.message_id)
        if event.message_id == "one":
            first_started.set()
            await release.wait()
        sequence.append("end:" + event.message_id)
        return "Resposta " + event.message_id

    async def accept(chat, message):
        sequence.append("send:" + message["text"])
        return {"message_id": "accepted"}
    value.set_message_handler(handler)
    monkeypatch.setattr(value, "send_api_message", accept)

    async def scenario():
        nonlocal first_started, release
        first_started, release = asyncio.Event(), asyncio.Event()
        app = web.Application(handler_args={"auto_decompress": False})
        app.router.add_post(value.webhook_path, value._handle_webhook)
        server, base = await listener(app)
        try:
            async with ClientSession() as client:
                async def post(identity, phone="+12025550191"):
                    body = payload(identity, phone=phone)
                    async with client.post(base + value.webhook_path, data=body, headers=signed(body)) as response:
                        assert response.status == 200
                await post("one")
                await asyncio.wait_for(first_started.wait(), 2)
                await post("two")
                await post("other", "+12025550192")
                for _ in range(100):
                    if "end:other" in sequence:
                        break
                    await asyncio.sleep(.01)
                assert "end:other" in sequence
                assert "start:two" not in sequence
                release.set()
                await asyncio.wait_for(drain(value), 3)
                assert sequence.index("send:Resposta one") < sequence.index("start:two")
                assert value.state.status()["jobs"] == {"completed": 3}
                assert not value._active_turn_context
        finally:
            release.set()
            await server.cleanup()
            await value.disconnect()
    asyncio.run(scenario())


def test_local_profile_upload_and_send_are_deduplicated_with_fresh_signed_links(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    (tmp_path / "media").mkdir()
    body = b"%PDF-1.7\nprofile-approved-catalog"
    (tmp_path / "media" / "catalog.pdf").write_bytes(body)
    identity = module("library").catalog(tmp_path)[0].id
    uploaded, sent, resolved = [], [], []

    async def upload(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        form = await request.multipart()
        part = await form.next()
        assert part.name == "file" and part.filename == "catalog.pdf"
        uploaded.append(bytes(await part.read()))
        return web.json_response({"data": {"key": "custom-fields/test/file", "name": "catalog.pdf", "size": len(body), "mimeType": "application/pdf"}})

    async def url(request):
        resolved.append(request.query["key"])
        return web.json_response({"data": {"url": "https://storage.example.org/catalog.pdf?signature=" + str(len(resolved))}})

    async def send(request):
        sent.append(await request.json())
        return web.json_response({"data": {"id": "message-" + str(len(sent)), "status": "queued"}})

    async def handler(event):
        message = {"type": "document", "fileId": identity, "caption": "Catálogo"}
        first = await value.send_api_message(event.source.chat_id, message)
        assert await value.send_api_message(event.source.chat_id, message) == first
        return "Segue o catálogo."
    value.set_message_handler(handler)

    async def scenario():
        api = web.Application()
        api.router.add_post("/api/v1/custom-fields/upload", upload)
        api.router.add_get("/api/v1/custom-fields/file", url)
        api.router.add_post("/api/v1/conversations/messages", send)
        server, base = await listener(api)
        value.api_base_url = base
        try:
            for identifier in ("one", "two"):
                data = json.loads(payload(identifier))["data"]
                job = value.state.admit({"payload": {"data": data}, "messages": data["messages"], "contact": data["contact"],
                    "channel": data["channel"], "phone": data["contact"]["phone"], "chat_id": "channel:+12025550191"}, now=time.time(), max_age=300)
                assert job
                value._schedule("channel:+12025550191")
                await asyncio.wait_for(drain(value), 5)
            assert uploaded == [body]
            assert len(resolved) == 2
            assert [m["type"] for m in sent] == ["document", "text", "document", "text"]
            assert sent[0]["mediaLink"] != sent[2]["mediaLink"]
            assert all(m["to"] == "+12025550191" and m["channelId"] == "channel" for m in sent)
            assert value.state.status()["actions"] == {"accepted": 5}
        finally:
            await server.cleanup()
            await value.disconnect()
    asyncio.run(scenario())


def test_backend_failure_after_send_is_uncertain_and_never_repeats_http_write(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    writes = []
    async def send(request):
        writes.append(await request.json())
        return web.json_response({"error": "upstream_failure_after_processing"}, status=500)
    async def handler(event):
        for _ in range(2):
            with pytest.raises(module("api").PipefacilAPIError):
                await value.send_api_message(event.source.chat_id, {"type": "text", "text": "hello"})
        return None
    value.set_message_handler(handler)
    async def scenario():
        app = web.Application()
        app.router.add_post("/api/v1/conversations/messages", send)
        server, base = await listener(app)
        value.api_base_url = base
        try:
            data = json.loads(payload())["data"]
            value.state.admit({"payload": {"data": data}, "messages": data["messages"], "contact": data["contact"],
                "channel": data["channel"], "phone": data["contact"]["phone"], "chat_id": "channel:+12025550191"}, now=time.time(), max_age=300)
            value._schedule("channel:+12025550191")
            await asyncio.wait_for(drain(value), 3)
            assert len(writes) == 1
            assert value.state.status()["actions"] == {"uncertain": 1}
            assert value.state.status()["jobs"] == {"failed": 1}
        finally:
            await server.cleanup()
            await value.disconnect()
    asyncio.run(scenario())


def test_queue_backpressure_rolls_back_receipts_and_health_requires_handler(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch, capacity=1)
    async def scenario():
        response = await value._handle_health(None)
        assert response.status == 503
        release, started = asyncio.Event(), asyncio.Event()
        async def handler(event):
            started.set()
            await release.wait()
            return None
        value.set_message_handler(handler)
        async def post(body):
            class Request:
                content_length = len(body)
                headers = signed(body)
                async def read(self):
                    return body
            return await value._handle_webhook(Request())
        try:
            assert (await post(payload("one"))).status == 200
            await asyncio.wait_for(started.wait(), 2)
            assert (await value._handle_health(None)).status == 200
            assert (await post(payload("two"))).status == 429
            with value.state.db() as db:
                assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
            release.set()
            await drain(value)
            assert (await post(payload("two"))).status == 200
            await drain(value)
        finally:
            release.set()
            await value.disconnect()
    asyncio.run(scenario())


def test_timeout_revokes_worker_capability_and_stops_native_task(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch, timeout=.05)
    attempts, effects = [], []
    async def handler(event):
        context = value._live_turn_context(event.source.chat_id)
        def late_worker():
            time.sleep(.12)
            try:
                value.effect(context["job_id"], "late", {}, lambda: effects.append("forbidden") or {},
                             active=lambda: value._live_turn_context(event.source.chat_id) is context)
            except module("api").PipefacilAPIError:
                attempts.append("revoked")
        await asyncio.to_thread(late_worker)
        return "must not send"
    value.set_message_handler(handler)
    async def scenario():
        try:
            data = json.loads(payload())["data"]
            value.state.admit({"payload": {"data": data}, "messages": data["messages"], "contact": data["contact"],
                "channel": data["channel"], "phone": data["contact"]["phone"], "chat_id": "channel:+12025550191"}, now=time.time(), max_age=300)
            value._schedule("channel:+12025550191")
            await asyncio.wait_for(drain(value), 2)
            await asyncio.sleep(.15)
            assert attempts == ["revoked"] and effects == []
            assert not value._session_tasks and not value._active_turn_context
            assert value.state.status()["jobs"] == {"failed": 1}
        finally:
            await value.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize('ambiguous_upload', [False, True])
def test_failed_media_preparation_allows_final_text_without_repeating_upload(tmp_path, monkeypatch, ambiguous_upload):
    value = adapter(tmp_path, monkeypatch)
    (tmp_path / 'media').mkdir()
    (tmp_path / 'media' / 'catalog.pdf').write_bytes(b'%PDF-1.7\nfixture')
    identity = module('library').catalog(tmp_path)[0].id
    uploads, sent = [], []

    async def upload(request):
        uploads.append(1)
        await request.read()
        if ambiguous_upload:
            return web.json_response({'error':'unknown_outcome'}, status=503)
        return web.json_response({'data':{'key':'storage/file'}})

    async def url(request):
        return web.json_response({'data':{'url':'http://storage.example.org/catalog.pdf'}})

    async def send(request):
        sent.append(await request.json())
        return web.json_response({'data':{'id':'message-'+str(len(sent)), 'status':'queued'}})

    async def handler(event):
        with pytest.raises(module('api').PipefacilAPIError):
            await value.send_api_message(event.source.chat_id, {'type':'document', 'fileId':identity})
        return 'Não consegui enviar o documento.'

    value.set_message_handler(handler)

    async def scenario():
        app = web.Application()
        app.router.add_post('/api/v1/custom-fields/upload', upload)
        app.router.add_get('/api/v1/custom-fields/file', url)
        app.router.add_post('/api/v1/conversations/messages', send)
        server, base = await listener(app)
        value.api_base_url = base
        try:
            for name in ['first', 'second']:
                data = json.loads(payload(name))['data']
                value.state.admit({'payload':{'data':data}, 'messages':data['messages'], 'contact':data['contact'],
                    'channel':data['channel'], 'phone':data['contact']['phone'], 'chat_id':'channel:+12025550191'},
                    now=time.time(), max_age=300)
                value._schedule('channel:+12025550191')
                await asyncio.wait_for(drain(value), 5)
            assert len(uploads) == 1
            assert len(sent) == 2 and all(m['type'] == 'text' for m in sent)
            assert value.state.status()['actions']['rejected'] == 2
            assert value.state.status()['actions'].get('uncertain', 0) == int(ambiguous_upload)
        finally:
            await server.cleanup()
            await value.disconnect()
    asyncio.run(scenario())
