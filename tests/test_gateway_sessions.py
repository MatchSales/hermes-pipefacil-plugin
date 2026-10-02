"""Regression tests against Hermes' real multiplexed SessionStore and SessionDB."""
from __future__ import annotations

import asyncio
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
    entries, adapters = {}, {}
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
            adapter._active_turn_context = {chat_id: [{"deal_seq": 106, "media_paths": frozenset()}]}
            adapter._history_reset_lock = threading.RLock()
            adapter._pending_reset_purges = {}
            adapter._reset_purge_results = {}
            adapter_module._ADAPTERS_BY_PROFILE[str(home.resolve())] = adapter
            adapters[name] = adapter
        yield SimpleNamespace(root=root, homes=homes, entries=entries, adapters=adapters,
                              store=store, tools=tools, module=adapter_module, chat_id=chat_id)
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
        reset_current_observability_context(ctx)
        reset_hermes_home_override(token)


def test_approved_reference_listing_cannot_expose_other_profile_files(multiplex):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    m = multiplex
    home = m.homes["sdr-a"]
    sid = m.entries["sdr-a"].session_id
    token = set_hermes_home_override(str(home))
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
    assert m.tools._active_pipefacil_chat(new.session_id, m.homes["sdr-a"]) == (m.chat_id, "")


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
    assert asyncio.run(adapter.send("chat", "Normal customer reply")).success
    assert sent == [{"type": "text", "text": "Normal customer reply"}]
