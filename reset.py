"""Pure helpers for Pipefacil reset commands and CRM history boundaries."""

from __future__ import annotations

import math
import unicodedata
from datetime import datetime
from typing import Any


def _timestamp_epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if not math.isfinite(timestamp):
            return None
        return timestamp / 1000 if timestamp > 100_000_000_000 else timestamp
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().timestamp()


def _normalize_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    normalized = dict(message)
    for target, aliases in (
        ("id", ("id", "messageId", "message_id")),
        ("externalId", ("externalId", "external_id", "externalID", "wamid")),
        ("body", ("body", "text", "content", "messageBody", "message_body")),
        ("type", ("type", "messageType", "message_type", "kind")),
        ("timestamp", ("timestamp", "messageTimestamp", "message_timestamp", "createdAt", "created_at")),
    ):
        if normalized.get(target) is None:
            for key in aliases:
                if normalized.get(key) is not None:
                    normalized[target] = normalized[key]
                    break
    return normalized


def _reset_command_index(messages: list[dict[str, Any]]) -> int | None:
    """Find the last inbound /reset in a batch; outbound team text is never a command."""
    matches = []
    for index, message in enumerate(messages):
        if message.get("fromMe") is True:
            continue
        normalized = _normalize_message(message) or message
        kind = str(normalized.get("type") or "").strip().casefold()
        body = normalized.get("body")
        if not isinstance(body, str) or kind not in {"", "text", "chat", "message"}:
            continue
        # WhatsApp may include invisible direction marks around a right-to-left command.
        command = "".join(char for char in body if unicodedata.category(char) != "Cf")
        if command.strip().casefold() == "/reset":
            matches.append(index)
    return matches[-1] if matches else None


def _messages_after_reset(messages: list[dict[str, Any]], reset_index: int) -> list[dict[str, Any]]:
    return [message for message in messages[reset_index + 1:] if message.get("fromMe") is not True]


def _history_after_reset(
    history: list[dict[str, Any]], marker: tuple[float, str],
) -> list[dict[str, Any]]:
    """Keep messages after the reset row, including later messages with the same timestamp."""
    cutoff_epoch, reset_message_id = marker
    if reset_message_id:
        for index, item in enumerate(history):
            normalized = _normalize_message(item) or item
            item_id = str(normalized.get("id") or normalized.get("externalId") or "")
            if item_id == reset_message_id:
                return history[index + 1:]
    return [
        item for item in history
        if (item_epoch := _timestamp_epoch((_normalize_message(item) or item).get("timestamp"))) is not None
        and item_epoch > cutoff_epoch
    ]
