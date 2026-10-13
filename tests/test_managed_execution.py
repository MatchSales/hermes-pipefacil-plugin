"""Managed execution v2 through real native adapter turns and loopback CRM HTTP."""

import asyncio
import copy
import json
import time
from datetime import datetime, timezone

import pytest
from aiohttp import web

from test_gateway_sessions import _modules
from test_http_runtime import adapter, drain, listener


def modules():
    package = _modules()[0].__package__
    return tuple(__import__(package + "." + name, fromlist=["*"]) for name in ("managed", "admission", "api", "lead_gate"))


def command(identity="one", channel="whatsapp-a", deal="deal-a"):
    now = time.time()
    execution = dict(protocolVersion=2, executionId=identity, agentId="agent-a", dealId=deal,
                     generation=3, agentVersion=4, channelId=channel, conversationId="conversation-" + channel,
                     expiresAt=datetime.fromtimestamp(now + 300, timezone.utc).isoformat())
    return dict(workspaceId="workspace-a", agentId="agent-a", profile="pf-agent-a", generation=5, revision=1,
                payload=dict(type="message.received", timestamp=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                             execution=execution, data=dict(channel={"id": channel}, deal={"id": deal, "seq": 1},
                             contact={"id": "contact-a", "phone": "+5585999991111", "name": "Synthetic"},
                             messages=[dict(id="message-" + identity, type="text", body="oi", fromMe=False, timestamp=int(now * 1000))])))


def managed_adapter(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    managed, admission, api, gate = modules()
    value._admission_control = admission.ProfileAdmissionControl(tmp_path, True)
    value._managed_publication = {"revision": 1, "skills": []}
    (tmp_path / ".pipefacil-runtime.json").write_text(json.dumps({k: command()[k] for k in ("workspaceId", "agentId", "profile", "generation", "revision")}))
    value.set_admission(False, value.management_status()["version"])
    # Restore real admission/history paths replaced by the legacy fixture helper.
    monkeypatch.setattr(_modules()[0], "check_lead", gate.check_lead)
    monkeypatch.setattr(_modules()[0], "fetch_conversation_history", api.fetch_conversation_history)
    value.crm.member_user_id = "bot-a"
    return value


@pytest.mark.parametrize("pause_during_generation", [False, True])
def test_two_whatsapps_same_deal_original_destinations_receipts_and_pause(tmp_path, monkeypatch, pause_during_generation):
    value = managed_adapter(tmp_path, monkeypatch)
    commands = {c["payload"]["execution"]["executionId"]: c for c in [command(), command("two", "whatsapp-b"), command("parallel", "whatsapp-b", "other-deal")]}
    started, ended, sent, reports, sessions = [], [], [], [], []
    receipts = {}
    paused = False

    async def crm(request):
        nonlocal paused
        assert request.headers["Authorization"] == "Bearer test-key"
        path = request.path
        if "/receipts/" in path:
            receipt = receipts.get(path.rsplit("/", 1)[-1])
            return web.json_response({"data": receipt} if receipt else {"error": "absent"}, status=200 if receipt else 404)
        identifier = path.split("/executions/")[1].split("/")[0]
        execution = commands[identifier]["payload"]["execution"]
        if path.endswith("/completion"):
            reports.append((identifier, await request.json()))
            return web.json_response({"data": {"ok": True}})
        if paused:
            return web.json_response({"error": "obsolete"}, status=409)
        if request.method == "GET" and path.endswith("/messages"):
            return web.json_response({"data": []})
        if request.method == "GET":
            return web.json_response({"data": {"execution": execution, "deal": {
                "id": execution["dealId"], "seq": 1, "status": "open", "responsibleUserId": "human-a",
                "coOwnerUserId": "human-b", "contact": commands[identifier]["payload"]["data"]["contact"]}}})
        assert path.endswith("/messages")
        body = await request.json()
        assert "to" not in body and "channelId" not in body
        assert body == {"type": "text", "body": "Olá " + identifier}
        sent.append((identifier, execution["channelId"], body))
        receipt = {"status": "APPLIED", "result": {"id": "out-" + identifier, "status": "queued"}}
        receipts[request.headers["Idempotency-Key"]] = receipt
        # A lost response after the provider accepted the effect must be reconciled via GET.
        if identifier == "one":
            request.transport.close()
            return web.Response()
        return web.json_response({"data": receipt})

    async def scenario():
        nonlocal paused
        begun, release = asyncio.Event(), asyncio.Event()
        async def handler(event):
            identifier = modules()[0].current()["execution"]["executionId"]
            started.append(identifier)
            sessions.append((event.source.chat_id, event.source.thread_id))
            if identifier == "one":
                begun.set()
                await release.wait()
            ended.append(identifier)
            return "Olá " + identifier
        value.set_message_handler(handler)
        app = web.Application()
        app.router.add_route("*", "/api/v1/ai-agent/{tail:.*}", crm)
        server, value.api_base_url = await listener(app)
        try:
            first = await value.accept_managed_execution(commands["one"])
            assert first["state"] == "ACCEPTED"
            await asyncio.wait_for(begun.wait(), 3)
            await value.accept_managed_execution(commands["two"])
            await value.accept_managed_execution(commands["parallel"])
            # Same channel's queue stays FIFO; the other deal is tested separately below.
            await asyncio.sleep(.05)
            assert "two" not in started
            if pause_during_generation:
                paused = True
            release.set()
            await asyncio.wait_for(drain(value), 6)
            assert (len(sent) == 0) if paused else (len(sent) == 3)
            if not paused:
                assert sent[0][1] == "whatsapp-a" and sent[1][1] == "whatsapp-b"
                assert ended.index("one") < started.index("two") + 1
                assert len(set(sessions)) == 2
                assert all(revision == "pf-revision-1" for _, revision in sessions)
                assert all(report["state"] == "COMPLETED" for _, report in reports)
            assert len(reports) == 3
            prior = value.managed_execution_receipt("one")
            assert (await value.accept_managed_execution(commands["one"])) == prior
            assert len(started) == (1 if paused else 3)
        finally:
            release.set()
            await value.disconnect()
            await server.cleanup()
    asyncio.run(scenario())


def test_admission_rejects_stale_scope_expired_messages_and_legacy_private_override(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path, monkeypatch)
    async def handler(event):
        pytest.fail("No invalid command may reach the model")
    value.set_message_handler(handler)
    async def scenario():
        for field in ("workspaceId", "agentId", "profile", "generation", "revision"):
            bad = command()
            bad[field] = "wrong"
            with pytest.raises(ValueError):
                await value.accept_managed_execution(bad)
        bad = command()
        bad["payload"]["data"]["messages"][0]["timestamp"] = int((time.time() - 700) * 1000)
        with pytest.raises(ValueError, match="expired"):
            await value.accept_managed_execution(bad)
        bad = command()
        bad["payload"]["execution"]["channelId"] = "wrong"
        with pytest.raises(ValueError):
            await value.accept_managed_execution(bad)
        value.set_admission(True, value.management_status()["version"])
        with pytest.raises(RuntimeError, match="admission_closed"):
            await value.accept_managed_execution(command())
        assert value.state.status()["jobs"] == {}
    try:
        asyncio.run(scenario())
    finally:
        value.state.close()


def test_durable_restart_never_reexecutes_started_turn_and_expiry_is_original(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path, monkeypatch)
    value.set_message_handler(lambda event: None)
    monkeypatch.setattr(value, "_schedule", lambda chat: None)
    c = command()
    async def scenario():
        await value.accept_managed_execution(c)
    asyncio.run(scenario())
    chat = "whatsapp-a:conversation-whatsapp-a"
    job, _ = value.state.next(chat)
    value.state.close()
    state = _modules()[0].State(tmp_path)
    try:
        state.acquire()
        state.recover()
        assert state.execution_receipt("one")["state"] == "INTERRUPTED"
        assert state.next(chat) is None
        assert state.pending_execution_reports() == ["one"]
        bad = copy.deepcopy(c)
        bad["payload"]["data"]["messages"][0]["body"] = "changed"
        with pytest.raises(RuntimeError):
            state.verify_execution("one", bad)
    finally:
        state.close()


def test_per_deal_lock_serializes_channels_but_allows_other_deals(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path, monkeypatch)
    managed = modules()[0]
    async def scenario():
        active = set()
        async def turn(deal, marker):
            kwargs = {"payload": {"_pipefacil_managed": {"workspaceId": "ws", "execution": {"dealId": deal}}}}
            async with managed.turn_scope(value, kwargs):
                assert deal not in active
                active.add(deal)
                await asyncio.sleep(.02)
                assert managed.current()["execution"]["dealId"] == deal
                active.remove(deal)
        await asyncio.gather(turn("same", 1), turn("same", 2), turn("other", 3))
        assert managed.current() is None
    try:
        asyncio.run(scenario())
    finally:
        value.state.close()


def test_managed_handoff_only_pauses_and_preserves_effect_receipt(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path, monkeypatch)
    managed, _, api, _ = modules()
    handoff = __import__(managed.__package__ + ".handoff", fromlist=["execute"])
    c = command()
    monkeypatch.setattr(value, "_schedule", lambda chat: None)
    value.set_message_handler(lambda event: None)
    calls = []
    receipt = None
    def request(**kwargs):
        nonlocal receipt
        calls.append(kwargs)
        if kwargs["method"] == "GET":
            if receipt is None:
                raise api.PipefacilAPIError("Absent", status_code=404)
            return {"data": receipt}
        assert kwargs["path"].endswith("/handoff")
        assert kwargs["payload"] is None
        receipt = {"status": "APPLIED", "result": {"agentId": "agent-a", "enabled": False, "generation": 4}}
        return {"data": receipt}
    monkeypatch.setattr(api, "request_json", request)
    monkeypatch.setattr(value.crm, "authorized", lambda *a, **k: {})
    async def scenario():
        await value.accept_managed_execution(c)
        job, kwargs = value.state.next("whatsapp-a:conversation-whatsapp-a")
        context = {"job_id": job, "deal_seq": 1, "contact": c["payload"]["data"]["contact"]}
        async with managed.turn_scope(value, kwargs):
            first = await handoff.execute(value, "whatsapp-a:conversation-whatsapp-a", context, "test-key", {}, lambda: True)
            assert first["paused"] and first["terminal"]
            assert await handoff.execute(value, "whatsapp-a:conversation-whatsapp-a", context, "test-key", {}, lambda: True) == first
            assert len([call for call in calls if call["method"] == "POST"]) == 1
            assert all("responsibleUserId" not in json.dumps(call) for call in calls)
    try:
        asyncio.run(scenario())
    finally:
        value.state.close()


@pytest.mark.parametrize("kind", ["image", "document", "audio"])
def test_managed_media_uses_execution_endpoint_and_v2_body_without_destination(monkeypatch, kind):
    managed, _, api, _ = modules()
    token = managed.CURRENT.set({"execution": {"executionId": "media-exec"}})
    calls = []
    def request(**kwargs):
        calls.append(kwargs)
        if kwargs["method"] == "GET":
            raise api.PipefacilAPIError("Absent", status_code=404)
        return {"data": {"status": "APPLIED", "result": {"id": "media-receipt"}}}
    monkeypatch.setattr(api, "request_json", request)
    try:
        assert api.send_message(api_key="test",base_url="https://crm.invalid",recipient="untrusted",
            channel_id="untrusted",message_type=kind,media_link="https://media.invalid/file",caption="File")["data"]["id"] == "media-receipt"
        write = calls[-1]
        assert write["path"] == "/api/v1/ai-agent/executions/media-exec/messages"
        assert write["payload"] == {"type":kind,"mediaUrl":"https://media.invalid/file","caption":"File"}
        assert write["idempotency_key"]
    finally:
        managed.CURRENT.reset(token)
