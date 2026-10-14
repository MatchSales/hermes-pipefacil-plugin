"""Real HMAC HTTP ingress and native turns for external profiles using execution v2."""

import asyncio
import copy
import json
import time
from datetime import datetime, timezone

from aiohttp import ClientSession, web
import pytest

from test_gateway_sessions import _modules
from test_hardening import module, signed
from test_http_runtime import adapter, drain, listener, payload, native_sessions
from test_managed_execution import command


def external_adapter(home, monkeypatch):
    value = adapter(home, monkeypatch)
    monkeypatch.setattr(_modules()[0], "check_lead", module("lead_gate").check_lead)
    monkeypatch.setattr(_modules()[0], "fetch_conversation_history", module("api").fetch_conversation_history)
    value.crm.member_user_id = "external-bot"
    value.crm.fields = frozenset({"name", "notes"})
    native_sessions(value)
    return value


@pytest.mark.parametrize("action", ["reply", "resume", "resume_history_unavailable", "mixed", "update", "handoff", "lost_response", "pause", "reassign", "revoke", "expired"])
def test_external_webhook_v2_uses_history_original_channel_and_live_authorization(tmp_path, monkeypatch, action):
    value = external_adapter(tmp_path, monkeypatch)
    event_payload = command()["payload"]
    execution = event_payload["execution"]
    if action in {"resume", "resume_history_unavailable"}:
        event_payload["type"] = "attendance.resume"
    if action == "mixed":
        event_payload["data"]["messages"].append({"id": "team-message", "type": "text",
            "body": "Equipe no lote misto", "fromMe": True, "timestamp": int(time.time() * 1000)})
    receipts, writes, history_reads = {}, [], []
    invalidated = False
    notes = "Original"

    async def crm(request):
        nonlocal notes
        assert request.headers["Authorization"] == "Bearer test-key"
        # Generic routes are forbidden in converted workspaces. A wrong route is
        # therefore a failed test, even if it would otherwise return a valid deal.
        assert request.path.startswith("/api/v1/ai-agent/")
        if "/receipts/" in request.path:
            result = receipts.get(request.path.rsplit("/", 1)[-1])
            return web.json_response({"data": result}, status=200 if result else 404)
        assert "/executions/one" in request.path
        if invalidated:
            return web.json_response({"error": action}, status=403 if action == "revoke" else 409)
        if request.method == "GET" and request.path.endswith("/messages"):
            history_reads.append(request.path)
            if action == "resume_history_unavailable":
                return web.json_response({"error": "unavailable"}, status=503)
            return web.json_response({"data": [dict(id="device-msg", direction="OUTBOUND", body="Resposta pelo celular", timestamp="2026-10-01T12:00:00Z")]})
        if request.method == "GET":
            return web.json_response({"data": {"execution": execution, "deal": dict(
                seq=1, status="open", name="Original", notes=notes, responsibleUserId="human-a", coOwnerUserId="human-b",
                contact=event_payload["data"]["contact"])}})
        body = await request.json() if request.can_read_body else None
        writes.append((request.path, body, request.headers["Idempotency-Key"]))
        if request.path.endswith("/handoff"):
            assert body is None
            result = dict(agentId=execution["agentId"], enabled=False, generation=4)
        elif request.path.endswith("/deal"):
            assert body == {"notes": "Atualização externa"}
            notes = body["notes"]
            result = {"seq": 1}
        else:
            assert request.path.endswith("/messages")
            assert body == {"type": "text", "body": "Resposta externa"}
            result = {"id": "out-one", "status": "queued"}
        receipts[request.headers["Idempotency-Key"]] = {"status": "APPLIED", "result": result}
        if action == "lost_response":
            request.transport.close()
            return web.Response()
        return web.json_response({"data": receipts[request.headers["Idempotency-Key"]]})

    async def scenario():
        nonlocal invalidated
        async def handler(event):
            nonlocal invalidated
            assert module("managed").current()["execution"] == execution
            assert "Equipe: Resposta pelo celular" in event.text
            assert "Equipe no lote misto" not in event.text
            assert "mantém responsável e co-responsável" in event.channel_prompt
            if action == "resume":
                assert "O operador autorizado do CRM solicitou retomar" in event.text
                assert "Mensagem(ns) nova(s) recebida(s) agora do lead" not in event.text
            if action == "handoff":
                result = await module("handoff").execute(value, event.source.chat_id,
                    event._pipefacil_turn_context, "test-key", {}, lambda: True)
                assert result["paused"]
            elif action == "update":
                result = await asyncio.to_thread(value.crm.update, event._pipefacil_turn_context,
                    "test-key", {"notes": "Atualização externa"}, lambda: True)
                assert result["updated"]
            elif action not in {"reply", "resume", "mixed", "lost_response"}:
                invalidated = True
            return "Resposta externa"
        value.set_message_handler(handler)
        crm_app = web.Application()
        crm_app.router.add_route("*", "/{tail:.*}", crm)
        crm_server, value.api_base_url = await listener(crm_app)
        ingress = web.Application(handler_args={"auto_decompress": False})
        ingress.router.add_post(value.webhook_path, value._handle_webhook)
        ingress_server, origin = await listener(ingress)
        raw = json.dumps(event_payload).encode()
        try:
            async with ClientSession() as client:
                async with client.post(origin + value.webhook_path, data=raw, headers=signed(raw)) as response:
                    assert response.status == 200 and (await response.json())["status"] == "accepted"
                await asyncio.wait_for(drain(value), 5)
                async with client.post(origin + value.webhook_path, data=raw, headers=signed(raw)) as response:
                    assert response.status == 200 and (await response.json())["status"] == "duplicate"
                assert len(history_reads) == (3 if action == "resume_history_unavailable" else 1)
                assert len(writes) == (2 if action == "update" else 1 if action in {"reply", "resume", "mixed", "handoff", "lost_response"} else 0)
                if action in {"pause", "reassign", "revoke", "expired", "resume_history_unavailable"}:
                    # A refused final send remains a failed private job, with no
                    # retry or fallback notice delivered to the customer.
                    assert value.state.status()["jobs"] == {"failed": 1}
                    assert writes == []
                else:
                    assert value.state.status()["jobs"] == {"completed": 1}
                assert value.management_status()["managed"] is False
                assert value.state.pending_execution_reports() == []
                assert module("managed").current() is None
        finally:
            await value.disconnect()
            await ingress_server.cleanup()
            await crm_server.cleanup()
    asyncio.run(scenario())


@pytest.mark.parametrize("change,status", [("version", 422), ("channel", 422), ("deal", 422),
    ("outbound", 422), ("expired", 200), ("private", 400), ("malformed", 422), ("signature", 401)])
def test_invalid_v2_webhook_never_falls_back_to_legacy(tmp_path, monkeypatch, change, status):
    value = adapter(tmp_path, monkeypatch)
    data = command()["payload"]
    if change == "version":
        data["execution"]["protocolVersion"] = 1
    elif change in {"channel", "deal"}:
        data["execution"][change + "Id"] = "wrong"
    elif change == "outbound":
        data["data"]["messages"][0]["fromMe"] = True
    elif change == "expired":
        data["execution"]["expiresAt"] = datetime.fromtimestamp(time.time() - 1, timezone.utc).isoformat()
    elif change == "private":
        data["_pipefacil_external_execution"] = {"execution": data["execution"]}
    elif change == "malformed":
        data["execution"] = None
    value._schedule = lambda _: pytest.fail("Invalid webhook cannot be queued")

    async def scenario():
        app = web.Application(handler_args={"auto_decompress": False})
        app.router.add_post(value.webhook_path, value._handle_webhook)
        server, origin = await listener(app)
        raw = json.dumps(data).encode()
        try:
            async with ClientSession() as client:
                async with client.post(origin + value.webhook_path, data=raw,
                    headers=signed(raw, secret="wrong" if change == "signature" else "literal-secret")) as response:
                    assert response.status == status
            assert value.state.status()["jobs"] == {}
        finally:
            await value.disconnect()
            await server.cleanup()
    asyncio.run(scenario())


def test_external_execution_duplicate_conflict_expiry_and_restart(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    value._schedule = lambda _: None
    data = command()["payload"]

    async def post(body):
        raw = json.dumps(body).encode()
        class Request:
            content_length = len(raw)
            headers = signed(raw)
            async def read(self):
                return raw
        return await value._handle_webhook(Request())

    async def scenario():
        assert (await post(data)).status == 200
        assert json.loads((await post(data)).body)["status"] == "duplicate"
        changed = copy.deepcopy(data)
        changed["execution"]["generation"] += 1
        assert (await post(changed)).status == 409
        changed = copy.deepcopy(data)
        changed["data"]["messages"][0]["id"] = "other"
        assert (await post(changed)).status == 409
        with value.state.db() as db:
            assert db.execute("SELECT expires FROM jobs").fetchone()[0] <= time.time() + 301
        job, kwargs = value.state.next("whatsapp-a:conversation-whatsapp-a")
        assert kwargs["payload"]["_pipefacil_external_execution"]["execution"] == data["execution"]
        value.state.close()
        value.state = _modules()[0].State(tmp_path)
        value.state.acquire()
        assert value.state.recover() == []
        assert json.loads((await post(data)).body)["status"] == "duplicate"
        assert value.state.status()["jobs"] == {"interrupted": 1}
        assert value.state.pending_execution_reports() == []
    try:
        asyncio.run(scenario())
    finally:
        value.state.close()


def test_legacy_webhook_still_has_no_execution_scope(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    received = []
    async def capture(**kwargs):
        received.append(kwargs["messages"])
        assert module("managed").current() is None
    value._process_event = capture
    async def scenario():
        raw = payload()
        class Request:
            content_length = len(raw)
            headers = signed(raw)
            async def read(self):
                return raw
        assert (await value._handle_webhook(Request())).status == 200
        await drain(value)
        assert len(received) == 1
    try:
        asyncio.run(scenario())
    finally:
        value.state.close()


def test_external_turns_serialize_same_deal_across_channels(tmp_path, monkeypatch):
    value = adapter(tmp_path, monkeypatch)
    started, active = [], set()
    release = None
    async def capture(**kwargs):
        execution = module("managed").current()["execution"]
        deal, identifier = execution["dealId"], execution["executionId"]
        assert deal not in active
        active.add(deal)
        started.append(identifier)
        if identifier == "one":
            await release.wait()
        active.remove(deal)
    value._process_event = capture
    async def scenario():
        nonlocal release
        release = asyncio.Event()
        async def post(c):
            raw = json.dumps(c["payload"]).encode()
            class Request:
                content_length = len(raw)
                headers = signed(raw)
                async def read(self):
                    return raw
            assert (await value._handle_webhook(Request())).status == 200
        await post(command())
        await post(command("two", "whatsapp-b"))
        await post(command("other", "whatsapp-c", "other-deal"))
        for _ in range(200):
            if {"one", "other"}.issubset(started):
                break
            await asyncio.sleep(.005)
        assert "one" in started and "other" in started and "two" not in started
        release.set()
        await asyncio.wait_for(drain(value), 3)
        assert "two" in started and active == set()
        assert module("managed").current() is None
    try:
        asyncio.run(scenario())
    finally:
        if release:
            release.set()
        value.state.close()
