"""Stable extension entry point; no client extension imports private loader namespaces."""

from dataclasses import dataclass
import json
from pathlib import Path

from .extensions import API_REVISION, PUBLIC_EVENT


@dataclass(frozen=True)
class StoredAudio:
    profile_home: str
    job_id: int
    storage_key: str
    content_digest: str


def stored_audio(adapter, context, receipt, content_digest):
    if not isinstance(receipt, dict) or not isinstance(receipt.get("key"), str) or not receipt["key"]:
        raise ValueError("Audio requires a verified upload receipt.")
    with adapter.state.db() as db:
        receipts = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT result FROM actions WHERE job=? AND kind IN ('upload','generate_voice') AND state='accepted'",
                (context["job_id"],),
            )
        ]
    if not any(r.get("key") == receipt["key"] for r in receipts):
        raise ValueError("Audio upload must be accepted in this exact profile and turn.")
    return StoredAudio(str(adapter.profile_home), context["job_id"], receipt["key"], content_digest)


def register(ctx):
    from hermes_constants import get_hermes_home
    from . import api, adapter, tools, security, state, network, handoff, library

    home = Path(get_hermes_home()).resolve()

    def provide(*, event_type="", profile_home="", **kwargs):
        if event_type != PUBLIC_EVENT or Path(profile_home).resolve() != home:
            return None
        return {
            "provider": "pipefacil-platform",
            "profile_home": str(home),
            "api_revision": API_REVISION,
            "api": api,
            "adapter": adapter,
            "tools": tools,
            "security": security,
            "state": state,
            "network": network,
            "handoff": handoff,
            "library": library,
            "stored_audio": stored_audio,
            "StoredAudio": StoredAudio,
        }

    ctx.register_hook("gateway_platform_event", provide)
