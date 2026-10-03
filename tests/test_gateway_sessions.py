"""Regression tests against Hermes' real multiplexed SessionStore and SessionDB."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


def _modules():
    name = "pipefacil_plugin_test"
    if name not in sys.modules:
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    return (
        __import__(f"{name}.adapter", fromlist=["*"]),
        __import__(f"{name}.tools", fromlist=["*"]),
    )


@pytest.fixture
def multiplex(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionSource, SessionStore
    from hermes_cli import profiles
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    root = tmp_path / "gateway"
    homes = {name: root / "profiles" / name for name in ("sdr-a", "sdr-b")}
    for home in (root, *homes.values()):
        home.mkdir(parents=True, exist_ok=True)
        (home / ".env").write_text("PIPEFACIL_API_KEY=test-key\n")
    monkeypatch.setattr(profiles, "profile_exists", lambda name: name in homes)
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: homes[name])
    # The plugin registry adds the custom enum at runtime; tests register the same value.
    platform = Platform._add_pseudo_member("pipefacil")
    token = set_hermes_home_override(str(root))
    store = SessionStore(root / "sessions", GatewayConfig(multiplex_profiles=True))
    adapter_module, tools = _modules()
    monkeypatch.setattr(adapter_module, "_ADAPTERS_BY_PROFILE", {})
    entries, adapters, contexts = {}, {}, {}
    chat_id = "channel:test-contact"
    try:
        for name, home in homes.items():
            scoped = set_hermes_home_override(str(home))
            try:
                entries[name] = store.get_or_create_session(SessionSource(
                    platform=platform, chat_id=chat_id, user_id="test-contact", profile=name,
                ))
            finally:
                reset_hermes_home_override(scoped)
            adapter = object.__new__(adapter_module.PipefacilAdapter)
            adapter.profile_home = home
            adapter.gateway_runner = SimpleNamespace(session_store=store)
            adapter._destinations = {chat_id: {"phone": "test-contact"}}
            adapter._turn_context_lock = threading.RLock()
            contexts[name] = {"deal_seq": 106, "media_paths": frozenset(), "session_key": entries[name].session_key}
            adapter._active_turn_context = {chat_id: [contexts[name]]}
            adapter._history_reset_lock = threading.RLock()
            adapter._pending_reset_purges = {}
            adapter._reset_purge_results = {}
            adapter_module._ADAPTERS_BY_PROFILE[str(home.resolve())] = adapter
            adapters[name] = adapter
        @contextmanager
        def turn(name):
            token = adapter_module._ACTIVE_PIPEFACIL_TURN.set((adapters[name], chat_id, contexts[name]))
            try:
                yield
            finally:
                adapter_module._ACTIVE_PIPEFACIL_TURN.reset(token)

        yield SimpleNamespace(root=root, homes=homes, entries=entries, adapters=adapters,
                              store=store, tools=tools, module=adapter_module, chat_id=chat_id, turn=turn)
    finally:
        store.close_all_db_handles()
        reset_hermes_home_override(token)


def test_live_route_is_resolved_when_routing_index_lives_in_gateway_home(multiplex):
    m = multiplex
    for name in m.homes:
        entry = m.entries[name]
        # This topology reproduced the production bug: only the root has gateway_routing.
        assert m.store._db_for_key(entry.session_key).gateway_routing_entry_for_session(entry.session_id) is None
        assert m.store._routing_db.gateway_routing_entry_for_session(entry.session_id) is not None
        with m.turn(name):
            assert m.tools._active_pipefacil_chat(entry.session_id, m.homes[name]) == (m.chat_id, "")
    assert m.tools._active_pipefacil_chat(m.entries["sdr-b"].session_id, m.homes["sdr-a"])[0] is None
    assert m.tools._active_pipefacil_chat("unknown-session", m.homes["sdr-a"])[0] is None
    from gateway.config import Platform
    from gateway.session import SessionSource
    operator = m.store.get_or_create_session(SessionSource(
        platform=Platform.LOCAL, chat_id="local", user_id="operator", profile="sdr-a",
    ))
    assert m.tools._active_pipefacil_chat(operator.session_id, m.homes["sdr-a"])[0] is None
    m.adapters["sdr-a"]._active_turn_context.clear()
    assert m.tools._active_pipefacil_chat(m.entries["sdr-a"].session_id, m.homes["sdr-a"])[0] is None


def test_all_three_tools_use_the_current_profile_route(multiplex, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.approval_context import reset_current_observability_context, set_current_observability_context
    m = multiplex
    home = m.homes["sdr-a"]
    (home / "knowledge").mkdir()
    (home / "knowledge" / "guide.txt").write_text("Profile A knowledge")
    sent, updates = [], []

    async def send(chat_id, message):
        sent.append((chat_id, message))
        return {"message_id": "accepted-test"}

    monkeypatch.setattr(m.adapters["sdr-a"], "send_api_message", send)
    monkeypatch.setattr(m.tools, "update_deal", lambda **kwargs: updates.append(kwargs))
    token = set_hermes_home_override(str(home))
    ctx = set_current_observability_context(turn_id="gateway-regression", session_id=m.entries["sdr-a"].session_id)
    active = m.turn("sdr-a")
    active.__enter__()
    try:
        sid = m.entries["sdr-a"].session_id
        assert "Profile A knowledge" in m.tools._read_profile_file({"path": "knowledge/guide.txt"}, session_id=sid)
        result = json.loads(asyncio.run(m.tools._send_messages(
            {"messages": [{"type": "text", "text": "Reply to the current lead"}]}, session_id=sid,
        )))
        assert result["accepted_by_api"] == 1
        assert sent[0][0] == m.chat_id
        assert json.loads(asyncio.run(m.tools._update_deal(
            {"properties": {"notes": "confirmed by the lead"}}, session_id=sid,
        )))["success"] is True
        assert updates[0]["seq"] == 106
    finally:
        active.__exit__(None, None, None)
        reset_current_observability_context(ctx)
        reset_hermes_home_override(token)


@pytest.mark.parametrize("final_text,expected_id", [("First message", "accepted-1"), ("First message\n\nSecond message", "accepted-2")])
def test_final_reply_does_not_repeat_api_accepted_text_from_the_same_turn(multiplex, monkeypatch, final_text, expected_id):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.approval_context import reset_current_observability_context, set_current_observability_context
    m = multiplex
    adapter = m.adapters["sdr-a"]
    sent = []

    async def send(chat_id, message):
        sent.append(message)
        return {"message_id": f"accepted-{len(sent)}"}

    monkeypatch.setattr(adapter, "send_api_message", send)
    home_token = set_hermes_home_override(str(m.homes["sdr-a"]))
    obs_token = set_current_observability_context(turn_id="final-reply-dedup", session_id=m.entries["sdr-a"].session_id)

    async def scenario():
        with m.turn("sdr-a"):
            result = json.loads(await m.tools._send_messages({"messages": [
                {"type": "text", "text": "First message"}, {"type": "text", "text": "Second message"},
            ]}, session_id=m.entries["sdr-a"].session_id))
            assert result["accepted_by_api"] == 2
            final = await adapter.send(m.chat_id, final_text, metadata={"notify": True})
            assert final.success and final.message_id == expected_id
            assert len(sent) == 2
            # A useful new final answer still sends; comparison is exact except for whitespace.
            assert (await adapter.send(m.chat_id, "A new qualifying question", metadata={"notify": True})).success
            assert len(sent) == 3
        # A completed event cannot trigger a delayed/proactive delivery.
        assert (await adapter.send(m.chat_id, final_text, metadata={"notify": True})).success
        assert len(sent) == 3

    try:
        asyncio.run(scenario())
    finally:
        reset_current_observability_context(obs_token)
        reset_hermes_home_override(home_token)


def test_approved_reference_listing_cannot_expose_other_profile_files(multiplex):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    m = multiplex
    home = m.homes["sdr-a"]
    sid = m.entries["sdr-a"].session_id
    token = set_hermes_home_override(str(home))
    active = m.turn("sdr-a")
    active.__enter__()
    try:
        assert json.loads(m.tools._read_profile_file({"path": "knowledge/"}, session_id=sid))["entries"] == []
        folder = home / "knowledge"
        folder.mkdir()
        (folder / "guide.txt").write_text("Approved facts")
        (folder / "more").mkdir()
        foreign = m.homes["sdr-b"] / "private.txt"
        foreign.write_text("Other profile data")
        (folder / "foreign.txt").symlink_to(foreign)
        (folder / "broken.txt").symlink_to(folder / "missing.txt")
        listed = json.loads(m.tools._read_profile_file({"path": "knowledge/"}, session_id=sid))
        assert listed["entries"] == [
            {"path": "knowledge/guide.txt", "type": "file"},
            {"path": "knowledge/more", "type": "directory"},
        ]
        for path in (".", "knowledge/foreign.txt", str(foreign)):
            assert "error" in json.loads(m.tools._read_profile_file({"path": path}, session_id=sid))
        assert "knowledge/" in m.tools._read_profile_file({"path": "*"}, session_id=sid)
    finally:
        active.__exit__(None, None, None)
        reset_hermes_home_override(token)


@pytest.mark.parametrize("guarded_signature", [False, True])
def test_reset_removes_only_the_rotated_profile_transcript(multiplex, monkeypatch, guarded_signature):
    m = multiplex
    old = m.entries["sdr-a"]
    adapter = m.adapters["sdr-a"]
    db = m.store._db_for_key(old.session_key)
    guard_calls = []
    if guarded_signature:
        legacy_delete = db.delete_session

        def guarded_delete(session_id, sessions_dir=None, *, exclude_active_write_guards=False):
            guard_calls.append(exclude_active_write_guards)
            return legacy_delete(session_id, sessions_dir=sessions_dir)

        monkeypatch.setattr(db, "delete_session", guarded_delete)
    db.append_message(old.session_id, "user", "Old test context")
    other = m.entries["sdr-b"]
    other_db = m.store._db_for_key(other.session_key)
    other_db.append_message(other.session_id, "user", "Other profile context")
    sessions = m.homes["sdr-a"] / "sessions"
    sessions.mkdir(exist_ok=True)
    artifacts = [sessions / f"{old.session_id}.json", sessions / f"{old.session_id}.jsonl",
                 sessions / f"request_dump_{old.session_id}_1.json"]
    for artifact in artifacts:
        artifact.write_text("old snapshot")
    preserved = sessions / f"{other.session_id}.json"
    preserved.write_text("other transcript")
    observability = m.root / "observability.json"
    observability.write_text("trace retained")
    adapter._pending_reset_purges[old.session_id] = old.session_key
    # A live route must never be deleted, even on the legacy unguarded API.
    assert adapter.purge_reset_session(old.session_id, sessions_dir=sessions) is True
    assert db.get_session(old.session_id) is not None
    assert adapter._reset_purge_results == {}
    new = m.store.reset_session(old.session_key)
    assert new.session_id != old.session_id
    assert adapter.purge_reset_session(old.session_id, sessions_dir=sessions) is True
    assert adapter._reset_purge_results[old.session_id] is True
    assert db.get_session(old.session_id) is None
    assert not any(artifact.exists() for artifact in artifacts)
    assert preserved.exists() and observability.read_text() == "trace retained"
    if guarded_signature:
        assert guard_calls == [True]
    assert db.get_session(new.session_id) is not None
    assert other_db.get_session(other.session_id) is not None
    assert m.tools._active_pipefacil_chat(old.session_id, m.homes["sdr-a"])[0] is None
    with m.turn("sdr-a"):
        assert m.tools._active_pipefacil_chat(new.session_id, m.homes["sdr-a"]) == (m.chat_id, "")


def test_real_background_dispatch_keeps_each_event_context_until_completion(multiplex):
    from contextvars import copy_context
    from dataclasses import replace
    from gateway.config import PlatformConfig
    from gateway.platforms.event import MessageType
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    m = multiplex
    token = set_hermes_home_override(str(m.homes["sdr-a"]))
    adapter = m.module.PipefacilAdapter(PlatformConfig(enabled=True, extra={}))
    adapter.gateway_runner = SimpleNamespace(session_store=m.store)
    adapter._destinations = {m.chat_id: {"phone": "test-contact"}}
    captured, seen = [], []

    async def scenario():
        first_started, release_first, second_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handler(event):
            if event.message_id == "first":
                first_started.set()
                await release_first.wait()
            captured.append(copy_context())
            context = await asyncio.to_thread(adapter.trusted_turn_context, m.chat_id)
            route = await asyncio.to_thread(m.tools._active_pipefacil_chat, m.entries["sdr-a"].session_id, adapter.profile_home)
            seen.append((event.message_id, context["deal_seq"], route))
            if event.message_id == "second":
                second_done.set()
            return None

        adapter.set_message_handler(handler)
        events = []
        for message_id, seq in (("first", 106), ("second", 206)):
            event = m.module._new_message_event(
                text="Customer message", message_type=MessageType.TEXT, user_id="test-contact",
                source=replace(m.entries["sdr-a"].origin, message_id=message_id), message_id=message_id,
                allow_gateway_control=False,
            )
            event._pipefacil_turn_context = {"deal_seq": seq, "media_paths": frozenset()}
            events.append(event)
        # Hermes returns before the model finishes. The second event is queued, not active yet.
        await adapter.handle_message(events[0])
        await asyncio.wait_for(first_started.wait(), 2)
        await adapter.handle_message(events[1])
        assert adapter.trusted_turn_context(m.chat_id) is None  # unrelated task cannot borrow facts
        release_first.set()
        await asyncio.wait_for(second_done.wait(), 2)
        await asyncio.gather(*list(adapter._session_tasks.values()))

    try:
        asyncio.run(scenario())
        assert seen == [("first", 106, (m.chat_id, "")), ("second", 206, (m.chat_id, ""))]
        assert adapter._active_turn_context == {}
        assert all(context.run(adapter.trusted_turn_context, m.chat_id) is None for context in captured)
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize("cancelled", [False, True])
def test_background_context_is_revoked_after_failure_or_cancellation(multiplex, monkeypatch, cancelled):
    from contextvars import copy_context
    from dataclasses import replace
    from gateway.config import PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageType
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    m = multiplex
    token = set_hermes_home_override(str(m.homes["sdr-a"]))
    adapter = m.module.PipefacilAdapter(PlatformConfig(enabled=True, extra={}))
    captured = []

    async def no_delivery(*args, **kwargs):
        return SendResult(success=True)

    monkeypatch.setattr(adapter, "send", no_delivery)

    async def scenario():
        started, never = asyncio.Event(), asyncio.Event()

        async def handler(event):
            captured.append(copy_context())
            assert adapter.trusted_turn_context(m.chat_id)["deal_seq"] == 106
            started.set()
            if cancelled:
                await never.wait()
            raise RuntimeError("Expected test failure")

        adapter.set_message_handler(handler)
        event = m.module._new_message_event(
            text="Customer message", message_type=MessageType.TEXT, user_id="test-contact",
            source=replace(m.entries["sdr-a"].origin, message_id="failing"), message_id="failing",
            allow_gateway_control=False,
        )
        event._pipefacil_turn_context = {"deal_seq": 106, "media_paths": frozenset()}
        await adapter.handle_message(event)
        await asyncio.wait_for(started.wait(), 2)
        tasks = list(adapter._session_tasks.values())
        if cancelled:
            for task in tasks:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    try:
        asyncio.run(scenario())
        assert adapter._active_turn_context == {}
        assert captured[0].run(adapter.trusted_turn_context, m.chat_id) is None
    finally:
        reset_hermes_home_override(token)


def test_gateway_operator_notices_are_not_sent_to_leads(multiplex, monkeypatch):
    from gateway.config import PlatformConfig
    m = multiplex
    adapter = m.module.PipefacilAdapter(PlatformConfig(enabled=True, extra={}))
    assert adapter.config.extra["notice_delivery"] == "private"
    sent = []

    async def send(chat_id, message):
        sent.append(message)
        return {"message_id": "normal-reply"}

    monkeypatch.setattr(adapter, "send_api_message", send)
    result = asyncio.run(adapter.send_private_notice("chat", "lead", "Type /sethome to configure Hermes"))
    assert result.success and sent == []
    assert asyncio.run(adapter.send("chat", "⚡ Interrupting current task")).success
    assert asyncio.run(adapter.send("chat", "Normal customer reply", metadata={"notify": True})).success
    assert sent == []


def test_real_hermes_busy_reply_is_suppressed_but_background_answer_is_delivered(multiplex, monkeypatch):
    from dataclasses import replace
    from gateway.config import PlatformConfig
    from gateway.platforms.event import MessageType
    from gateway.run import GatewayRunner
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    m = multiplex
    token = set_hermes_home_override(str(m.homes["sdr-a"]))
    adapter = m.module.PipefacilAdapter(PlatformConfig(enabled=True, extra={}))
    adapter.gateway_runner = SimpleNamespace(session_store=m.store)
    sent = []

    async def send(chat_id, message):
        sent.append(message)
        return {"message_id": "customer-answer"}

    monkeypatch.setattr(adapter, "send_api_message", send)
    # Avoid the core's separate boot-recovery ledger in this isolated regression.
    async def no_obligation(*args):
        return None
    monkeypatch.setattr(adapter, "_record_delivery_obligation", no_obligation)
    event = m.module._new_message_event(
        text="Oi", message_type=MessageType.TEXT, user_id="test-contact",
        source=replace(m.entries["sdr-a"].origin, message_id="fresh"), message_id="fresh",
        allow_gateway_control=False,
    )
    event._pipefacil_turn_context = {"deal_seq": 106, "media_paths": frozenset()}

    async def scenario():
        async def handler(current):
            # Even inside an active turn the native busy notice has no reply marker.
            runner = object.__new__(GatewayRunner)
            await runner._send_busy_reply(current, adapter, "⚡ Interrupting current task. I'll respond shortly.\n💡 First-time tip: /busy queue")
            assert sent == []
            return "Oi! Como posso ajudar?"
        adapter.set_message_handler(handler)
        await adapter.handle_message(event)
        await asyncio.gather(*list(adapter._session_tasks.values()))
        assert sent == [{"type": "text", "text": "Oi! Como posso ajudar?"}]
        await adapter.send(m.chat_id, "Late recovery reply", metadata={"notify": True})
        assert len(sent) == 1

    try:
        asyncio.run(scenario())
    finally:
        reset_hermes_home_override(token)
