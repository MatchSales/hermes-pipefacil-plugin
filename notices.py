"""Recognize Hermes-authored failed-turn boundaries on the customer delivery path."""

from __future__ import annotations


# Older supported hosts do not expose agent.turn_failure_copy. Keep their exact
# boundary copy here rather than matching generic words like "error" in a reply.
_FAILED_TURN_NOTICES = (
    "Your request was not processed. Send it again if you still want me to carry it out.",
    "This turn did not complete. Some actions may already have run; verify their effects before resending.",
)


def is_failed_turn_response(content: str) -> bool:
    """Drop the entire terminal diagnostic, including any provider detail before it.

    Hermes appends this boundary even when it labels the final delivery as a live
    reply. A quoted notice inside ordinary customer prose is not a failed turn.
    """
    try:
        from agent.turn_failure_copy import FAILED_TURN_NOTICE, PARTIAL_FAILED_TURN_NOTICE
        notices = (FAILED_TURN_NOTICE, PARTIAL_FAILED_TURN_NOTICE)
    except ImportError:
        notices = _FAILED_TURN_NOTICES
    text = content.strip().replace("\r\n", "\n")
    return any(text == notice or text.endswith("\n\n" + notice) for notice in notices)
