"""Native gateway turns + real SessionStore + CRM HTTP; no model/account/phone traffic."""
import asyncio
import importlib

import pytest
from aiohttp import web
from test_managed_execution import managed_adapter, command, modules
from test_http_runtime import listener, drain


@pytest.mark.parametrize("interrupt_generation", [False, True])
@pytest.mark.parametrize("partial_delivery", [False, True])
@pytest.mark.parametrize("metadata_first", [False, True])
def test_discarded_answer_never_enters_next_execution_history(tmp_path, monkeypatch, interrupt_generation, partial_delivery, metadata_first):
    value = managed_adapter(tmp_path / "profile", monkeypatch)
    store = value.gateway_runner.session_store
    first, second = command("old"), command("new")
    commands = {"old": first, "new": second}
    prompts, prior, sent, session_ids, session_dbs = [], [], [], [], []
    superseded = False
    context = importlib.import_module(modules()[0].__package__ + ".turn_context")
    monkeypatch.setattr(context, "AUTH_CHECK_INTERVAL", .025 if interrupt_generation else 2)

    async def crm(request):
        if "/receipts/" in request.path:
            return web.json_response({"error": "absent"}, status=404)
        identifier = request.path.split("/executions/")[1].split("/")[0]
        if request.path.endswith("/completion"):
            return web.json_response({"data": {"ok": True}})
        if identifier == "old" and superseded:
            return web.json_response({"error": "obsolete"}, status=409)
        if request.method == "GET" and request.path.endswith("/messages"):
            return web.json_response({"data": [{"id": "human", "direction": "outbound", "body": "CONFIRMED_HUMAN_REPLY"},
                *[{"id": str(i), "direction": "outbound", "body": text} for i, (_, text) in enumerate(sent)]]})
        if request.method == "GET":
            return web.json_response({"data": {"execution": commands[identifier]["payload"]["execution"],
                "deal": {"id": "deal-a", "seq": 1, "status": "open", "contact": first["payload"]["data"]["contact"]}}})
        body = await request.json()
        sent.append((identifier, body["body"]))
        return web.json_response({"data": {"status": "APPLIED", "result": {"id": "sent-new", "status": "sent"}}})

    async def scenario():
        nonlocal superseded
        begun, release = asyncio.Event(), asyncio.Event()
        interrupted = []
        def interrupt(*args, **kwargs):
            interrupted.append(args)
            release.set()  # Model/provider acknowledges the native hard interrupt.
        value.gateway_runner._interrupt_running_turn = interrupt
        async def model(event):
            identifier = modules()[0].current()["execution"]["executionId"]
            entry = store.get_or_create_session(event.source)
            session_ids.append(entry.session_id)
            session_dbs.append(store._db_for_key(entry.session_key))
            prior.append(store.load_transcript(entry.session_id))
            prompts.append(event.text)
            if metadata_first and identifier == "old":
                store.append_to_transcript(entry.session_id, {"role": "session_meta", "model": "synthetic", "tools": []})
            store.append_to_transcript(entry.session_id, {"role": "user", "content": event.text, "message_id": event.message_id})
            if identifier == "old":
                if partial_delivery:
                    await value.send_api_message(event.source.chat_id, {"type": "text", "text": "CONFIRMED_PARTIAL"})
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
            if not interrupt_generation:
                release.set()
            await asyncio.wait_for(drain(value), 12)
            assert sent == ([("old", "CONFIRMED_PARTIAL")] if partial_delivery else []) + [("new", "CONFIRMED_CURRENT_ANSWER")]
            assert len(prior) == 2
            assert not any("GHOST_UNSENT_ANSWER" in str(item) for item in prior[1]), prior[1]
            assert session_ids[0] == session_ids[1]
            assert "GHOST_UNSENT_ANSWER" in str(session_dbs[0].get_messages(session_ids[0], include_inactive=True))  # audit retained
            assert "CONFIRMED_HUMAN_REPLY" in prompts[1]
            assert "GHOST_UNSENT_ANSWER" not in prompts[1]
            if partial_delivery:
                assert "CONFIRMED_PARTIAL" in prompts[1]
            assert value.managed_execution_receipt("new")["state"] == "COMPLETED"
            if interrupt_generation:
                assert interrupted
        finally:
            release.set()
            await value.disconnect()
            await server.cleanup()
            store.close_all_db_handles()
    asyncio.run(scenario())


def test_many_turns_reuse_one_session_and_preserve_confirmed_history(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path, monkeypatch)
    store = value.gateway_runner.session_store
    commands = {str(i): command(str(i)) for i in range(20)}
    sessions, sent, priors = [], [], []

    async def crm(request):
        if "/receipts/" in request.path:
            return web.json_response({"error": "absent"}, status=404)
        identifier = request.path.split("/executions/")[1].split("/")[0]
        if request.path.endswith("/completion"):
            return web.json_response({"data": {"ok": True}})
        if request.method == "GET" and request.path.endswith("/messages"):
            return web.json_response({"data": [{"id": str(i), "direction": "outbound", "body": text} for i, text in enumerate(sent)]})
        if request.method == "GET":
            return web.json_response({"data": {"execution": commands[identifier]["payload"]["execution"],
                "deal": {"seq": 1, "status": "open", "contact": commands[identifier]["payload"]["data"]["contact"]}}})
        if identifier == "10":
            return web.json_response({"error": "obsolete"}, status=409)
        body = await request.json()
        sent.append(body["body"])
        return web.json_response({"data": {"status": "APPLIED", "result": {"id": "sent-" + identifier}}})

    async def scenario():
        async def model(event):
            entry = store.get_or_create_session(event.source)
            sessions.append(entry.session_id)
            priors.append(store.load_transcript(entry.session_id))
            identifier = modules()[0].current()["execution"]["executionId"]
            text = "UNSENT" if identifier == "10" else "CONFIRMED-" + identifier
            store.append_to_transcript(entry.session_id, {"role": "user", "content": event.text, "message_id": event.message_id})
            store.append_to_transcript(entry.session_id, {"role": "assistant", "content": text})
            return text
        value.set_message_handler(model)
        app = web.Application()
        app.router.add_route("*", "/api/v1/ai-agent/{tail:.*}", crm)
        server, value.api_base_url = await listener(app)
        try:
            for cmd in commands.values():
                await value.accept_managed_execution(cmd)
                await asyncio.wait_for(drain(value), 6)
            assert len(set(sessions)) == 1
            assert len(sent) == 19
            assert "UNSENT" not in str(priors[11:])
            assert "CONFIRMED-9" in str(priors[11])
            assert "CONFIRMED-18" in str(priors[19])
        finally:
            await value.disconnect()
            await server.cleanup()
            store.close_all_db_handles()
    asyncio.run(scenario())


@pytest.mark.parametrize("blocker", ["none", "uncertain", "live_worker", "changed_prefix", "another_turn"])
def test_restart_recovery_keeps_audit_and_blocks_unsafe_reuse(tmp_path, monkeypatch, blocker):
    value = managed_adapter(tmp_path, monkeypatch)
    managed = modules()[0]
    context = importlib.import_module(managed.__package__ + ".turn_context")
    store = value.gateway_runner.session_store
    cmd = command()
    identity = {k: cmd[k] for k in ("workspaceId", "agentId", "profile", "generation", "revision")}
    identity["execution"] = cmd["payload"]["execution"]
    token = managed.CURRENT.set(identity)
    try:
        with value._runtime_scope():
            source = value._build_contact_source(chat_id="conversation", contact=cmd["payload"]["data"]["contact"], message_id="one")
            entry = store.get_or_create_session(source)
            db = store._db_for_key(entry.session_key)
            store.append_to_transcript(entry.session_id, {"role": "user", "content": "Confirmed question"})
            store.append_to_transcript(entry.session_id, {"role": "assistant", "content": "Confirmed answer"})
            scope = context.prepare(value, source, 1)
            store.append_to_transcript(entry.session_id, {"role": "user", "content": "Interrupted question", "message_id": source.message_id})
            store.append_to_transcript(entry.session_id, {"role": "assistant", "content": "UNSENT"})
            if blocker == "uncertain":
                with value.state.db() as journal:
                    journal.execute("INSERT INTO actions VALUES ('uncertain',1,'send','uncertain',NULL,0)")
            elif blocker == "live_worker":
                assert db.try_acquire_session_turn_lease(entry.session_id, "test-native-worker")
            elif blocker == "changed_prefix":
                db.archive_and_compact(entry.session_id, [{"role": "user", "content": "Changed by concurrent compaction"}])
            elif blocker == "another_turn":
                store.append_to_transcript(entry.session_id, {"role": "user", "content": "Other request", "message_id": "unrelated-input"})
            # Simulate the journal reopening after process death, not an in-memory flag.
            value.state.close()
            value.state = importlib.import_module(managed.__package__ + ".state").State(tmp_path)
            value.state.acquire()
            if blocker != "none":
                with pytest.raises(Exception):
                    context.prepare(value, source, 2)
                assert value.state.turn_checkpoint(scope)["job"] == 1
                if blocker in {"changed_prefix", "another_turn"}:
                    return
                if blocker == "live_worker":
                    db.release_session_turn_lease(entry.session_id, "test-native-worker")
                else:
                    value.state.reconcile("uncertain", "not_performed", None, "Verified no provider delivery")
            assert context.prepare(value, source, 2) == scope
            transcript = store.load_transcript(entry.session_id)
            assert "Confirmed answer" in str(transcript)
            assert "UNSENT" not in str(transcript)
            assert "UNSENT" in str(db.get_messages(entry.session_id, include_inactive=True))
            assert store.get_or_create_session(source).session_id == entry.session_id
    finally:
        managed.CURRENT.reset(token)
        value.state.close()
        store.close_all_db_handles()


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


def test_uncertain_delivery_blocks_only_its_deal_until_reconciled(tmp_path, monkeypatch):
    value = managed_adapter(tmp_path, monkeypatch)
    store = value.gateway_runner.session_store
    commands = {c["payload"]["execution"]["executionId"]: c for c in [
        command("old"), command("blocked"), command("other", "whatsapp-b", "deal-b"), command("recovered")]}
    generated, delivered, prompts = [], [], []

    async def crm(request):
        if "/receipts/" in request.path:
            return web.json_response({"error": "receipt unavailable"}, status=404)
        identifier = request.path.split("/executions/")[1].split("/")[0]
        if request.path.endswith("/completion"):
            return web.json_response({"data": {"ok": True}})
        if request.method == "GET" and request.path.endswith("/messages"):
            return web.json_response({"data": [{"id": str(i), "direction": "outbound", "body": text} for i, text in enumerate(delivered)]})
        if request.method == "GET":
            return web.json_response({"data": {"execution": commands[identifier]["payload"]["execution"],
                "deal": {"seq": 1, "status": "open", "contact": commands[identifier]["payload"]["data"]["contact"]}}})
        body = await request.json()
        delivered.append(body["body"])
        if identifier == "old":
            # Provider delivered, but HTTP confirmation and its receipt are missing.
            return web.json_response({"error": "unknown outcome"}, status=500)
        return web.json_response({"data": {"status": "APPLIED", "result": {"id": "sent-" + identifier}}})

    async def scenario():
        async def model(event):
            identifier = modules()[0].current()["execution"]["executionId"]
            generated.append(identifier)
            prompts.append(event.text)
            entry = store.get_or_create_session(event.source)
            store.append_to_transcript(entry.session_id, {"role": "user", "content": event.text, "message_id": event.message_id})
            store.append_to_transcript(entry.session_id, {"role": "assistant", "content": "ANSWER-" + identifier})
            return "ANSWER-" + identifier
        value.set_message_handler(model)
        app = web.Application()
        app.router.add_route("*", "/api/v1/ai-agent/{tail:.*}", crm)
        server, value.api_base_url = await listener(app)
        try:
            for identifier in ("old", "blocked", "other"):
                await value.accept_managed_execution(commands[identifier])
                await asyncio.wait_for(drain(value), 8)
            assert generated == ["old", "other"]
            assert delivered == ["ANSWER-old", "ANSWER-other"]
            with value.state.db() as journal:
                keys = [r[0] for r in journal.execute("SELECT key FROM actions WHERE state='uncertain'")]
                assert len(keys) == 1
            value.state.reconcile(keys[0], "accepted", {"message_id": "verified-old"}, "Provider receipt matched CRM history")
            await value.accept_managed_execution(commands["recovered"])
            await asyncio.wait_for(drain(value), 8)
            assert generated == ["old", "other", "recovered"]
            assert delivered == ["ANSWER-old", "ANSWER-other", "ANSWER-recovered"]
            assert "ANSWER-old" in prompts[-1]
            assert value.managed_execution_receipt("recovered")["state"] == "COMPLETED"
        finally:
            await value.disconnect()
            await server.cleanup()
            store.close_all_db_handles()
    asyncio.run(scenario())
