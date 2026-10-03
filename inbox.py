"""Profile-scoped, durable admission of new Pipefacil messages."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_MESSAGE_AGE_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 30
RECEIPT_RETENTION_SECONDS = 7 * 24 * 3600


def message_epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        epoch = float(value)
        epoch = epoch / 1000 if epoch > 100_000_000_000 else epoch
        return epoch if math.isfinite(epoch) else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        # A timezone is required: the server's local timezone must not decide freshness.
        if parsed.tzinfo is None:
            return None
        epoch = parsed.timestamp()
        return epoch if math.isfinite(epoch) else None
    except (ValueError, OverflowError, OSError):
        return None


def fresh_messages(messages: list[dict[str, Any]], *, now: float, max_age: int) -> list[dict[str, Any]]:
    return [message for message in messages
            if (epoch := message_epoch(message.get("timestamp"))) is not None
            and -MAX_FUTURE_SKEW_SECONDS <= now - epoch <= max_age]


def claim_messages(
    profile_home: Path, chat_id: str, messages: list[dict[str, Any]], *, now: float,
) -> list[dict[str, Any]]:
    """Commit claims before dispatch; a restart or /reset cannot admit them again.

    No message contents or phone numbers are stored. A failed transaction raises, so the
    webhook can return 503 without invoking the agent. Claims survive processing failures:
    after admission the send outcome may be ambiguous, so replay must not send it twice.
    """
    directory = profile_home / "pipefacil-state"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "inbox.sqlite3"
    accepted = []
    connection = sqlite3.connect(path, timeout=5)
    try:
        path.chmod(0o600)
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, admitted_at REAL NOT NULL)")
            connection.execute("DELETE FROM receipts WHERE admitted_at < ?", (now - RECEIPT_RETENTION_SECONDS,))
            for message in messages:
                identity = message.get("id") or message.get("externalId")
                if not identity:
                    identity = {"body": message.get("body"), "timestamp": message.get("timestamp"),
                                "type": message.get("type"), "media": message.get("media")}
                raw = json.dumps([chat_id, identity], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                key = hashlib.sha256(raw.encode()).hexdigest()
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO receipts (key, admitted_at) VALUES (?, ?)", (key, now),
                ).rowcount
                if inserted:
                    accepted.append(message)
        return accepted
    finally:
        connection.close()
