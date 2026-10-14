"""Reusable v2 sessions with durable, exact-turn rollback and cooperative cancellation.

CRM is authoritative for delivered messages. A rewind only changes Hermes' active
context; it never deletes CRM messages, effect receipts, or archived transcript rows.
"""
import asyncio
import contextlib
import json
from pathlib import Path
import time

from . import api, managed
from .inbox import message_epoch
from .state import digest

AUTH_CHECK_INTERVAL = 2.0
STOP_TIMEOUT = 10.0


class ObsoleteTurn(RuntimeError):
    pass


def _store(adapter):
    runner = adapter.gateway_runner
    store = getattr(runner, "session_store", None)
    if store is None or not callable(getattr(runner, "_evict_cached_agent", None)):
        raise RuntimeError("Native session recovery unavailable")
    return store


def _database(adapter, store, key):
    db = store._db_for_key(key)
    if db is None or Path(db.db_path).resolve() != adapter.profile_home.resolve() / "state.db":
        raise RuntimeError("Session recovery requires the owning profile database")
    if not callable(getattr(db, "rewind_to_message", None)):
        raise RuntimeError("Runtime lacks guarded transcript rewind")
    return db


def _recover(adapter, saved):
    """Called under the deal lock; native CAS and write leases guard other writers."""
    store = _store(adapter)
    db = _database(adapter, store, saved["session_key"])
    sid = saved["session_id"]
    # A cancelled asyncio waiter does not imply that its HTTP/tool thread exited.
    if adapter.state.has_uncertain_actions(saved["job"]):
        raise RuntimeError("Session requires effect reconciliation")
    db._execute_write(lambda conn: db._check_transcript_write_guards(
        conn, sid, None, reject_active_turn_lease=True, reject_active_compression_lock=True))
    if (store._follow_reroutes(sid) != sid or (db.get_compression_tip(sid) or sid) != sid):
        raise RuntimeError("Session continuation changed; recovery requires review")
    before = json.loads(saved["active_ids"])
    active = db.get_active_message_ids(sid)
    if active[:len(before)] != before:
        raise RuntimeError("Session prefix changed; recovery requires review")
    added = active[len(before):]
    if added:
        rows = db.get_messages(sid, after_id=added[0] - 1)
        if (not rows or rows[0].get("role") != "user"
                or rows[0].get("platform_message_id") != saved["input_id"]
                or any(row.get("role") == "user" and row.get("platform_message_id") not in {None, saved["input_id"]}
                       for row in rows)):
            raise RuntimeError("Session suffix does not belong to this execution")
        # Address the exact first row after the durable checkpoint, never /undo 1
        # against a potentially newer turn. Native rewind soft-deletes the suffix.
        db.rewind_to_message(sid, added[0], expected_active_ids=active)
        if db.get_active_message_ids(sid) != before:
            raise RuntimeError("Session recovery did not restore its checkpoint")
    with store._get_transcript_drain_lock():
        store._clear_dirty_transcript(sid)
    adapter.gateway_runner._evict_cached_agent(saved["session_key"])
    adapter.state.clear_turn_checkpoint(saved["scope"], saved["job"])


def prepare(adapter, source, job):
    value = managed.current()
    if value is None:
        return None
    execution = value["execution"]
    scope = digest([value.get("workspaceId", str(adapter.profile_home)), execution["agentId"], execution["dealId"]])
    saved = adapter.state.turn_checkpoint(scope)
    if saved:
        _recover(adapter, saved)
    store = _store(adapter)
    entry = store.get_or_create_session(source)
    db = _database(adapter, store, entry.session_key)
    sid = db.get_compression_tip(entry.session_id) or entry.session_id
    adapter.state.save_turn_checkpoint(scope, job, entry.session_key, sid, db.get_active_message_ids(sid), source.message_id)
    return scope


def finish(adapter, scope, job, successful):
    if scope is None:
        return
    saved = adapter.state.turn_checkpoint(scope)
    if saved is None or saved["job"] != job:
        raise RuntimeError("Session checkpoint ownership changed")
    if successful and not adapter.state.has_uncertain_actions(job):
        adapter.state.clear_turn_checkpoint(scope, job)
    else:
        _recover(adapter, saved)


async def _watch_authorization(adapter, event, job):
    execution = managed.current()["execution"]
    expires = message_epoch(execution["expiresAt"])
    while True:
        await asyncio.sleep(AUTH_CHECK_INTERVAL)
        if not event._pipefacil_turn_context.get("active", True):
            return False
        # Handoff is terminal. Never issue a CRM read after it, including polling.
        if adapter.state.handoff(job):
            return False
        if expires is not None and time.time() >= expires:
            return True
        try:
            await asyncio.to_thread(managed.context, api_key=adapter.api_key,
                                    base_url=adapter.api_base_url, timeout=3.0)
        except api.PipefacilAPIError as exc:
            if exc.status_code in {401, 403, 404, 409, 410}:
                return True
            # Transport/5xx/429 is not evidence of revocation. The send-side fence
            # still fails closed, and the bounded turn deadline remains in force.


async def wait(adapter, event, task, session_key, job):
    if managed.current() is None:
        await asyncio.shield(task)
        return
    watcher = asyncio.create_task(_watch_authorization(adapter, event, job))
    try:
        completed, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in completed and watcher.result():
            event._pipefacil_turn_context["active"] = False
            adapter.gateway_runner._interrupt_running_turn(
                session_key, interrupt_reason="Pipefacil execution superseded",
                invalidation_reason="pipefacil_execution_superseded", tool_reason="Pipefacil cancellation")
            # Cooperative interrupt lets the actual worker finish and release its
            # durable write lease. Cancelling only the waiter would be unsafe.
            await asyncio.wait_for(asyncio.shield(task), timeout=STOP_TIMEOUT)
            raise ObsoleteTurn("Pipefacil execution is no longer authorized")
        await asyncio.shield(task)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
