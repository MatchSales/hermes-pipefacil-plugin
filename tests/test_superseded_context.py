"""Native gateway turns + real SessionStore + CRM HTTP; no model/account/phone traffic."""
import asyncio
from aiohttp import web
from gateway.config import GatewayConfig
from gateway.session import SessionStore
from test_managed_execution import managed_adapter, command, modules
from test_http_runtime import listener, drain


def test_discarded_answer_never_enters_next_execution_history(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path / "profile", monkeypatch)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    first, second = command("old"), command("new")
    commands = {"old": first, "new": second}
    prompts, prior, sent, session_ids, session_dbs = [], [], [], [], []
    superseded = False

    async def crm(request):
        if "/receipts/" in request.path:
            return web.json_response({"error": "absent"}, status=404)
        identifier = request.path.split("/executions/")[1].split("/")[0]
        if request.path.endswith("/completion"):
            return web.json_response({"data": {"ok": True}})
        if identifier == "old" and superseded:
            return web.json_response({"error": "obsolete"}, status=409)
        if request.method == "GET" and request.path.endswith("/messages"):
            return web.json_response({"data": [{"id": "human", "direction": "outbound", "body": "CONFIRMED_HUMAN_REPLY"}]})
        if request.method == "GET":
            return web.json_response({"data": {"execution": commands[identifier]["payload"]["execution"],
                "deal": {"id": "deal-a", "seq": 1, "status": "open", "contact": first["payload"]["data"]["contact"]}}})
        body = await request.json()
        sent.append((identifier, body["body"]))
        return web.json_response({"data": {"status": "APPLIED", "result": {"id": "sent-new", "status": "sent"}}})

    async def scenario():
        nonlocal superseded
        begun, release = asyncio.Event(), asyncio.Event()
        async def model(event):
            identifier = modules()[0].current()["execution"]["executionId"]
            entry = store.get_or_create_session(event.source)
            session_ids.append(entry.session_id)
            session_dbs.append(store._db_for_key(entry.session_key))
            prior.append(store.load_transcript(entry.session_id))
            prompts.append(event.text)
            if identifier == "old":
                begun.set()
                await release.wait()
            # Hermes persists model output before the platform finishes delivering it.
            text = "GHOST_UNSENT_ANSWER" if identifier == "old" else "CONFIRMED_CURRENT_ANSWER"
            store.append_to_transcript(entry.session_id, {"role": "assistant", "content": text})
            return text
        value.set_message_handler(model)
        app = web.Application()
        app.router.add_route("*", "/api/v1/ai-agent/{tail:.*}", crm)
        server, value.api_base_url = await listener(app)
        try:
            await value.accept_managed_execution(first)
            await asyncio.wait_for(begun.wait(), 3)
            superseded = True
            await value.accept_managed_execution(second)
            release.set()
            await asyncio.wait_for(drain(value), 12)
            assert sent == [("new", "CONFIRMED_CURRENT_ANSWER")]
            assert len(prior) == 2
            assert not any("GHOST_UNSENT_ANSWER" in str(item) for item in prior[1]), prior[1]
            assert session_ids[0] != session_ids[1]
            assert "GHOST_UNSENT_ANSWER" in str(session_dbs[0].get_messages(session_ids[0], include_inactive=True))  # audit retained
            assert "CONFIRMED_HUMAN_REPLY" in prompts[1]
            assert "GHOST_UNSENT_ANSWER" not in prompts[1]
            assert value.managed_execution_receipt("new")["state"] == "COMPLETED"
        finally:
            release.set()
            await value.disconnect()
            await server.cleanup()
            store.close_all_db_handles()
    asyncio.run(scenario())


def test_managed_history_failure_never_uses_local_memory(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path / "profile", monkeypatch)
    cmd = command("history-unavailable")
    generated, sent = [], []

    async def crm(request):
        if request.path.endswith("/completion"):
            return web.json_response({"data": {"ok": True}})
        if request.method == "GET" and request.path.endswith("/messages"):
            return web.json_response({"error": "history unavailable"}, status=503)
        if request.method == "GET":
            return web.json_response({"data": {"execution": cmd["payload"]["execution"], "deal": {
                "id": "deal-a", "seq": 1, "status": "open", "contact": cmd["payload"]["data"]["contact"]}}})
        sent.append(await request.json())
        return web.json_response({"data": {"status": "APPLIED", "result": {"id": "should-never-send"}}})

    async def scenario():
        async def model(event):
            generated.append(event.text)
            return "UNCONFIRMED_LOCAL_FALLBACK"
        value.set_message_handler(model)
        app = web.Application()
        app.router.add_route("*", "/api/v1/ai-agent/{tail:.*}", crm)
        server, value.api_base_url = await listener(app)
        try:
            await value.accept_managed_execution(cmd)
            await asyncio.wait_for(drain(value), 8)
            assert generated == [] and sent == []
            assert value.managed_execution_receipt("history-unavailable")["state"] == "FAILED"
        finally:
            await value.disconnect()
            await server.cleanup()
    asyncio.run(scenario())
