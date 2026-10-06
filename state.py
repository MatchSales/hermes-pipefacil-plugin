"""Atomic admission and effect journal. Only never-started jobs are recovered automatically."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

from .inbox import RECEIPT_RETENTION_SECONDS, message_epoch


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class StateError(RuntimeError):
    pass


class Full(StateError):
    pass


class Conflict(StateError):
    pass


class State:
    def __init__(self, home, *, capacity=500, per_chat=50):
        self.directory = Path(home).resolve() / "pipefacil-state"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise StateError("invalid_state_directory")
        self.directory.chmod(0o700)
        self.path = self.directory / "inbox.sqlite3"
        if self.path.is_symlink():
            raise StateError("invalid_state_file")
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        self.capacity, self.per_chat = capacity, per_chat
        self.lock = threading.RLock()
        self.lease = None
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts(key TEXT PRIMARY KEY, admitted_at REAL NOT NULL)")
            if "fingerprint" not in {r[1] for r in db.execute("PRAGMA table_info(receipts)")}:
                db.execute("ALTER TABLE receipts ADD COLUMN fingerprint TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT NOT NULL, "
                       "payload TEXT NOT NULL, state TEXT NOT NULL, admitted REAL NOT NULL, expires REAL NOT NULL, "
                       "updated REAL NOT NULL, error TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,chat,id)")
            db.execute("CREATE TABLE IF NOT EXISTS actions(key TEXT PRIMARY KEY, job INTEGER NOT NULL, kind TEXT NOT NULL, "
                       "state TEXT NOT NULL, result TEXT, updated REAL NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS actions_job_kind_state ON actions(job,kind,state)")
            db.execute("CREATE TABLE IF NOT EXISTS uploads(key TEXT PRIMARY KEY, receipt TEXT NOT NULL, updated REAL NOT NULL)")

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA secure_delete=ON")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def acquire(self):
        if self.lease is not None:
            return
        fd = os.open(self.directory / "consumer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise StateError("another_gateway_owns_this_profile") from None
        self.lease = fd

    def close(self):
        if self.lease is not None:
            os.close(self.lease)
            self.lease = None

    def recover(self):
        now = time.time()
        with self.db() as db:
            db.execute("UPDATE jobs SET state='interrupted',error='gateway_restarted',updated=? WHERE state='processing'", (now,))
            db.execute("UPDATE actions SET state='uncertain',updated=? WHERE state='pending'", (now,))
            db.execute("UPDATE jobs SET state='expired',error='queue_expired',updated=? WHERE state='queued' AND expires<?", (now, now))
            db.execute("DELETE FROM receipts WHERE admitted_at<?", (now - RECEIPT_RETENTION_SECONDS,))
            db.execute("DELETE FROM actions WHERE job IN (SELECT id FROM jobs WHERE state NOT IN ('queued','processing') AND updated<?) "
                       "AND state NOT IN ('pending','uncertain')", (now - RECEIPT_RETENTION_SECONDS,))
            db.execute("DELETE FROM jobs WHERE state NOT IN ('queued','processing') AND updated<? AND id NOT IN (SELECT job FROM actions)",
                       (now - RECEIPT_RETENTION_SECONDS,))
            return [r[0] for r in db.execute("SELECT DISTINCT chat FROM jobs WHERE state='queued'")]

    def prune(self, *, now=None):
        now = time.time() if now is None else now
        cutoff = now - RECEIPT_RETENTION_SECONDS
        with self.db() as db:
            db.execute("DELETE FROM receipts WHERE admitted_at<?", (cutoff,))
            db.execute("DELETE FROM uploads WHERE updated<?", (cutoff,))
            db.execute("DELETE FROM actions WHERE state NOT IN ('pending','uncertain') AND updated<?", (cutoff,))
            db.execute("DELETE FROM jobs WHERE state NOT IN ('queued','processing') AND updated<? AND id NOT IN (SELECT job FROM actions)", (cutoff,))
            # Unresolved effects keep their audit references, without retaining the
            # customer's raw webhook beyond the documented seven-day period.
            db.execute("UPDATE jobs SET payload='{}',chat='retained-audit-reference' WHERE state NOT IN ('queued','processing') AND admitted<?", (cutoff,))

    def admit(self, kwargs, *, now, max_age):
        chat = kwargs["chat_id"]
        with self.db() as db:
            accepted = []
            for message in kwargs["messages"]:
                identity = message.get("id") or message.get("externalId")
                if not identity:
                    identity = {k: message.get(k) for k in ("body", "timestamp", "type", "media")}
                key = digest([chat, identity])
                # Temporary signed URLs may change on a backend retry. Message identity,
                # content, type and original timestamp must remain identical.
                fingerprint = digest({k: message.get(k) for k in ("body", "timestamp", "type", "fromMe")})
                receipt = db.execute("SELECT fingerprint FROM receipts WHERE key=?", (key,)).fetchone()
                if receipt:
                    if receipt[0] and receipt[0] != fingerprint:
                        raise Conflict("message_id_reused_with_different_content")
                    continue
                db.execute("INSERT INTO receipts VALUES (?,?,?)", (key, now, fingerprint))
                accepted.append(message)
            if not accepted:
                return None
            total = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','processing')").fetchone()[0]
            count = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','processing') AND chat=?", (chat,)).fetchone()[0]
            if total >= self.capacity or count >= self.per_chat:
                raise Full("inbound_queue_full")  # Entire transaction, including receipts, rolls back.
            kwargs = {**kwargs, "messages": accepted}
            expires = min(message_epoch(m.get("timestamp")) + max_age for m in accepted)
            cursor = db.execute("INSERT INTO jobs(chat,payload,state,admitted,expires,updated) VALUES (?,?,'queued',?,?,?)",
                                (chat, canonical(kwargs), now, expires, now))
            return cursor.lastrowid

    def next(self, chat):
        now = time.time()
        with self.db() as db:
            db.execute("UPDATE jobs SET state='expired',error='queue_expired',updated=? WHERE chat=? AND state='queued' AND expires<?",
                       (now, chat, now))
            row = db.execute("SELECT * FROM jobs WHERE chat=? AND state='queued' ORDER BY id LIMIT 1", (chat,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='processing',updated=? WHERE id=?", (now, row["id"]))
            return row["id"], json.loads(row["payload"])

    def finish(self, job, status, error=None):
        if status not in {"completed", "failed", "interrupted"}:
            raise StateError("invalid_job_state")
        with self.db() as db:
            if status == "completed" and db.execute("SELECT 1 FROM actions WHERE job=? AND state IN ('pending','uncertain') LIMIT 1", (job,)).fetchone():
                status, error = "failed", "action_outcome_uncertain"
            db.execute("UPDATE jobs SET state=?,error=?,updated=? WHERE id=? AND state='processing'",
                       (status, error, time.time(), job))

    def claim_action(self, job, kind, arguments):
        key = digest([kind, arguments] if kind == "upload" else [job, kind, arguments])
        with self.db() as db:
            terminal = db.execute("SELECT key,state FROM actions WHERE job=? AND kind='handoff' LIMIT 1", (job,)).fetchone()
            if terminal and (kind == "handoff" and terminal["key"] != key
                             or kind != "handoff" and terminal["state"] in {"pending", "accepted", "uncertain"}):
                raise StateError("handoff_is_terminal: no further writes or sends in this turn")
            old = db.execute("SELECT state,result FROM actions WHERE key=?", (key,)).fetchone()
            if old:
                if old["state"] == "accepted":
                    return key, json.loads(old["result"])
                raise StateError("action_" + old["state"] + ": operator reconciliation required; request was not repeated")
            if kind == "send" and db.execute("SELECT 1 FROM actions WHERE job=? AND kind='send' AND state IN ('pending','uncertain') LIMIT 1", (job,)).fetchone():
                # Native retry/Markdown fallback may change the arguments. An
                # ambiguous send blocks all further sends in this exact turn.
                raise StateError("prior_send_uncertain: operator reconciliation required; request was not repeated")
            if kind == "handoff" and db.execute("SELECT 1 FROM actions WHERE job=? AND state IN ('pending','uncertain') LIMIT 1", (job,)).fetchone():
                raise StateError("prior_action_uncertain: finish reconciliation before transferring the deal")
            active = db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()
            if active is None or active[0] != "processing":
                raise StateError("turn_is_no_longer_active")
            db.execute("INSERT INTO actions(key,job,kind,state,updated) VALUES (?,?,?,'pending',?)", (key, job, kind, time.time()))
            return key, None

    def handoff(self, job):
        """Durable terminal state, including a receipt that survives loss of CRM access."""
        with self.db() as db:
            row = db.execute("SELECT key,state,result FROM actions WHERE job=? AND kind='handoff' LIMIT 1", (job,)).fetchone()
        if row is None:
            return None
        return {"key": row["key"], "state": row["state"],
                "result": json.loads(row["result"]) if row["result"] else None}

    def assert_handoff_ready(self, job):
        with self.db() as db:
            if db.execute("SELECT 1 FROM actions WHERE job=? AND state IN ('pending','uncertain') LIMIT 1", (job,)).fetchone():
                raise StateError("prior_action_uncertain: finish reconciliation before preparing handoff")

    def finish_action(self, key, result=None, *, rejected=False):
        with self.db() as db:
            db.execute("UPDATE actions SET state=?,result=?,updated=? WHERE key=? AND state='pending'",
                       ("accepted" if result is not None else "rejected" if rejected else "uncertain",
                        canonical(result) if result is not None else None, time.time(), key))

    def upload(self, key, receipt=None):
        with self.db() as db:
            if receipt is not None:
                db.execute("INSERT OR REPLACE INTO uploads VALUES (?,?,?)", (key, canonical(receipt), time.time()))
                return receipt
            row = db.execute("SELECT receipt FROM uploads WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def status(self):
        with self.db() as db:
            jobs = {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM jobs GROUP BY state")}
            actions = {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM actions GROUP BY state")}
        return {"jobs": jobs, "actions": actions, "capacity": self.capacity, "per_chat_capacity": self.per_chat}

    def reconcile(self, key, outcome, result, evidence):
        if self.lease is None:
            raise StateError("stop_gateway_and_acquire_profile_lease_first")
        if outcome not in {"accepted", "not_performed"} or not evidence.strip() or len(evidence) > 1000:
            raise StateError("invalid_reconciliation")
        with self.db() as db:
            row = db.execute("SELECT * FROM actions WHERE key=?", (key,)).fetchone()
            if row is None or row["state"] not in {"pending", "uncertain"}:
                raise StateError("action_does_not_need_reconciliation")
            if outcome == "accepted":
                expected = "key" if row["kind"] == "upload" else "message_id" if row["kind"] == "send" else "updated"
                if not isinstance(result, dict) or not result.get(expected):
                    raise StateError("accepted_reconciliation_requires_verified_receipt")
                if row["kind"] == "handoff" and (not result.get("handed_off") or not result.get("terminal")
                                                  or not result.get("responsibleUserId") or not result.get("seq")):
                    raise StateError("handoff_reconciliation_requires_verified_transfer_receipt")
            db.execute("CREATE TABLE IF NOT EXISTS reconciliations(action TEXT, outcome TEXT, evidence TEXT, at REAL)")
            db.execute("INSERT INTO reconciliations VALUES (?,?,?,?)", (key, outcome, evidence, time.time()))
            # 'not_performed' remains terminal. Reconciliation never re-runs an LLM
            # turn or HTTP write; a new customer event is required for a new send.
            db.execute("UPDATE actions SET state=?,result=?,updated=? WHERE key=?",
                       ("accepted" if outcome == "accepted" else "reconciled_not_performed",
                        canonical(result) if outcome == "accepted" else None, time.time(), key))
