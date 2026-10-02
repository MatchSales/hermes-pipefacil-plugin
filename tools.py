"""CRM update tool used by Pipefacil-triggered Hermes conversations."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from gateway.platforms._shared import get_scoped_secret

from .api import (
    DEFAULT_API_BASE_URL,
    PipefacilAPIError,
    normalize_api_base_url,
    update_deal,
)
from .media import resolve_media_link

logger = logging.getLogger(__name__)

_DEAL_FIELDS = {
    "name",
    "value",
    "currency",
    "closeProbability",
    "expectedCloseAt",
    "stageId",
    "lostReason",
    "notes",
    "tagIds",
    "customFields",
    "contact",
}


def _api_base_url() -> str:
    from hermes_cli.config import load_config_readonly

    config = load_config_readonly() or {}
    platform = (config.get("platforms") or {}).get("pipefacil") or {}
    extra = platform.get("extra") if isinstance(platform, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    return normalize_api_base_url(extra.get("api_base_url", DEFAULT_API_BASE_URL))


def _pipefacil_available() -> bool:
    return bool(get_scoped_secret("PIPEFACIL_API_KEY", "").strip())


async def _update_deal(args: dict[str, Any], *, session_id: str = "", **kwargs: Any) -> str:
    from tools.registry import tool_error, tool_result
    from hermes_constants import get_hermes_home

    profile_home = Path(get_hermes_home()).resolve()
    chat_id, routing_error = _active_pipefacil_chat(session_id, profile_home)
    if routing_error:
        return tool_error(routing_error)
    from .adapter import adapter_for_profile

    adapter = adapter_for_profile(profile_home, chat_id)
    context = adapter.trusted_turn_context(chat_id) if adapter is not None else None
    seq = context.get("deal_seq") if context is not None else None
    if not isinstance(seq, int) or seq <= 0:
        return tool_error("The current Pipefacil event has no deal to update.")
    properties = args.get("properties")
    if not isinstance(properties, dict) or not properties:
        return tool_error("Provide at least one CRM field to update in properties.")
    unknown = sorted(set(properties) - _DEAL_FIELDS)
    if unknown:
        return tool_error(f"Unsupported deal fields: {', '.join(unknown)}")
    api_key = get_scoped_secret("PIPEFACIL_API_KEY", "") or ""
    try:
        await asyncio.to_thread(
            update_deal,
            api_key=api_key,
            base_url=_api_base_url(),
            seq=seq,
            properties=properties,
        )
    except PipefacilAPIError as exc:
        return tool_error(str(exc), status_code=exc.status_code)
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc))
    return tool_result({"success": True, "seq": seq, "updated_fields": sorted(properties)})


def _read_profile_file(
    args: dict[str, Any], *, session_id: str = "", task_id: str = "default", **kwargs: Any,
) -> str:
    """Read approved profile knowledge or an attachment from this active lead turn."""
    from hermes_constants import get_hermes_home
    from tools.registry import tool_error, tool_result

    profile_home = Path(get_hermes_home()).resolve()
    chat_id, routing_error = _active_pipefacil_chat(session_id, profile_home)
    if routing_error:
        return tool_error(routing_error)
    from .adapter import adapter_for_profile

    adapter = adapter_for_profile(profile_home, chat_id)
    context = adapter.trusted_turn_context(chat_id) if adapter is not None else None
    if context is None:
        return tool_error("There is no active Pipefacil turn to read files for.")
    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        return tool_error("Provide a file path from the current attachment or the profile knowledge folder.")
    if any(character in raw_path for character in "*?[]"):
        return tool_error("Use an exact file path, or path='knowledge/' to list approved reference files. Wildcards are not supported.")
    candidate = Path(raw_path.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = profile_home / candidate
    knowledge_root = profile_home / "knowledge"
    if candidate == knowledge_root and not knowledge_root.exists():
        return tool_result({"path": "knowledge/", "entries": [], "note": "This profile has no approved reference files yet."})
    try:
        resolved = candidate.resolve(strict=True)
        if resolved.is_relative_to(knowledge_root) and resolved.is_dir():
            entries = []
            for entry in sorted(resolved.iterdir(), key=lambda item: item.name):
                try:
                    target = entry.resolve(strict=True)
                except (OSError, RuntimeError, ValueError):
                    continue
                if target.is_relative_to(knowledge_root) and (target.is_file() or target.is_dir()):
                    entries.append({"path": str(entry.relative_to(profile_home)), "type": "directory" if target.is_dir() else "file"})
                if len(entries) == 200:
                    break
            return tool_result({"path": str(resolved.relative_to(profile_home)), "entries": entries, "limit": 200})
        approved = (
            resolved.is_relative_to(knowledge_root)
            or str(resolved) in context["media_paths"]
        )
        if not approved or not resolved.is_file() or resolved.stat().st_size > 25 * 1024 * 1024:
            return tool_error("Reading this file is not allowed for the current Pipefacil conversation.")
    except (OSError, RuntimeError, ValueError):
        return tool_error("The requested file is unavailable or outside this profile.")
    try:
        offset = int(args.get("offset", 1))
        limit = int(args.get("limit", 200))
    except (TypeError, ValueError):
        return tool_error("offset and limit must be integers.")
    if offset < 1 or not 1 <= limit <= 200:
        return tool_error("Use offset >= 1 and limit between 1 and 200.")
    from tools.file_tools import read_file_tool

    return read_file_tool(str(resolved), offset=offset, limit=limit, task_id=task_id)


def _active_pipefacil_chat(session_id: str, profile_home: Path) -> tuple[str | None, str]:
    """Resolve a live gateway route and verify its profile and current webhook turn.

    Multiplexed Hermes stores routing in the launch profile's database, while
    transcripts belong to each routed profile. SessionStore owns both identities.
    """
    if not session_id:
        return None, "This tool requires an active Pipefacil conversation."
    from .adapter import adapter_for_profile

    profile_home = Path(profile_home).resolve()
    adapter = adapter_for_profile(profile_home)
    runner = getattr(adapter, "gateway_runner", None)
    store = getattr(runner, "session_store", None)
    if store is None:
        return None, "The active Pipefacil gateway is unavailable; no action was taken."
    try:
        entry = store.lookup_by_session_id(session_id)
    except Exception:
        logger.warning("[pipefacil] Could not resolve the active gateway session", exc_info=True)
        return None, "Hermes session routing is unavailable; no action was taken."
    origin = getattr(entry, "origin", None)
    platform = getattr(origin, "platform", None)
    if getattr(platform, "value", platform) != "pipefacil":
        return None, "The active Hermes session is not a Pipefacil conversation."
    try:
        # Hermes selects the owner from the route key, independent of the worker's
        # ambient context. This shared handle belongs to the gateway: never close it.
        owner_db = store._db_for_key(entry.session_key)
        owner_path = Path(owner_db.db_path).resolve() if owner_db is not None else None
    except Exception:
        logger.warning("[pipefacil] Could not verify the gateway session's profile", exc_info=True)
        return None, "The Pipefacil session's profile could not be verified; no action was taken."
    if owner_path != profile_home / "state.db":
        return None, "The active Pipefacil session belongs to a different Hermes profile."
    chat_id = getattr(origin, "chat_id", None)
    if not isinstance(chat_id, str) or not chat_id:
        return None, "The active Pipefacil conversation has no trusted destination."
    context = adapter.trusted_turn_context(chat_id)
    if (
        adapter_for_profile(profile_home, chat_id) is not adapter
        or context is None
        or context.get("session_key") != entry.session_key
    ):
        return None, "There is no active Pipefacil webhook turn for this conversation; no action was taken."
    return chat_id, ""


def _prepare_outbound_message(item: Any, profile_home: Path) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Each message must be an object.")
    kind = item.get("type")
    caption = item.get("caption")
    if caption is not None and (not isinstance(caption, str) or len(caption) > 1000):
        raise ValueError("A caption must be text of at most 1000 characters.")
    if kind == "text":
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("Text messages must contain 1–4000 characters.")
        if caption is not None or item.get("url") is not None:
            raise ValueError("Text messages accept only the text field.")
        return {"type": "text", "text": text}
    if kind not in {"image", "document"}:
        raise ValueError("Message type must be text, image, or document.")
    url = item.get("url")
    if not isinstance(url, str):
        raise ValueError("An image or document requires a URL listed in this profile's SOUL.md.")
    library_item = resolve_media_link(profile_home, kind=kind, url=url)
    if library_item is None:
        raise ValueError("That HTTPS URL and type are not listed in this profile's SOUL.md.")
    parsed = urlsplit(library_item.url)
    filename = unquote(parsed.path.rsplit("/", 1)[-1]).strip()
    mime_type = mimetypes.guess_type(filename or parsed.path)[0]
    message: dict[str, Any] = {"type": kind, "mediaLink": library_item.url}
    if caption:
        message["caption"] = caption
    if kind == "document":
        filename = filename or library_item.label
        filename = filename.replace("\\", "_").replace("/", "_").replace("\r", " ").replace("\n", " ")[:180]
        message["filename"] = filename
    if mime_type:
        message["mimeType"] = mime_type
    return message


async def _send_messages(
    args: dict[str, Any], *, session_id: str = "", **kwargs: Any,
) -> str:
    from tools.registry import tool_error, tool_result

    items = args.get("messages")
    if not isinstance(items, list) or not 1 <= len(items) <= 2:
        return tool_error("Provide one or two messages; the agent's final response is sent automatically afterward.")
    try:
        from hermes_constants import get_hermes_home

        profile_home = Path(get_hermes_home()).resolve()
        prepared = [_prepare_outbound_message(item, profile_home) for item in items]
    except (OSError, TypeError, ValueError) as exc:
        return tool_error(f"No message was sent: {exc}")

    chat_id, routing_error = _active_pipefacil_chat(session_id, profile_home)
    if routing_error:
        return tool_error(routing_error)
    from .adapter import adapter_for_profile, reserve_preliminary_messages

    adapter = adapter_for_profile(profile_home, chat_id)
    if adapter is None:
        return tool_error("No active Pipefacil destination exists for this Hermes session; no message was sent.")
    try:
        # Hermes currently injects session_id/task_id into plugin tool handlers but not turn_id.
        # The tool-dispatch observability context is per-turn and is bound for every registry call.
        from tools import approval_context

        turn_id = approval_context._approval_turn_id.get()
    except (AttributeError, ImportError):
        turn_id = ""
    if not reserve_preliminary_messages(profile_home, turn_id, len(prepared)):
        return tool_error(
            "This Hermes turn can send at most two preliminary messages in total. "
            "The final assistant response is sent automatically afterward."
        )

    accepted = []
    for index, message in enumerate(prepared, start=1):
        try:
            result = await adapter.send_api_message(chat_id, message)
        except PipefacilAPIError as exc:
            if accepted:
                return tool_error(
                    f"Partial result: Pipefacil accepted {len(accepted)} of {len(prepared)} requested messages; "
                    f"message {index} failed ({exc}). Do not claim the failed message was sent."
                )
            return tool_error(f"Pipefacil did not accept message {index}: {exc}")
        accepted.append({"index": index, "status": "accepted_by_api", "message_id": result.get("message_id")})
    return tool_result({
        "success": True,
        "accepted_by_api": len(accepted),
        "delivery_confirmed": False,
        "messages": accepted,
        "note": "The final assistant response is sent automatically after this tool call.",
    })


def register_tools(ctx) -> None:
    name = "pipefacil_update_deal"
    ctx.register_tool(
        name=name,
        toolset="pipefacil",
        description=(
            "Update fields of the deal in the CURRENT Pipefacil event. The deal seq is "
            "resolved by the plugin, never supplied by the model. Only update when the conversation gives reliable evidence "
            "for the change. For a stage move pass its exact stageId; a lost stage also requires lostReason. "
            "Never use this to mark a deal won or lost based only on a promise or an inference."
        ),
        schema={
            "name": name,
            "description": "Partially update one Pipefacil deal in this profile's workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "properties": {
                        "type": "object",
                        "description": "Fields to patch; workspaceId is deliberately not accepted.",
                        "properties": {
                            "name": {"type": "string"},
                            "value": {"type": "number", "minimum": 0},
                            "currency": {"type": "string", "maxLength": 3},
                            "closeProbability": {"type": "integer", "minimum": 0, "maximum": 100},
                            "expectedCloseAt": {"type": "string", "format": "date-time"},
                            "stageId": {"type": "string"},
                            "lostReason": {"type": "string", "maxLength": 255},
                            "notes": {"type": "string", "maxLength": 2000},
                            "tagIds": {"type": "array", "items": {"type": "string"}},
                            "customFields": {"type": "object", "additionalProperties": True},
                            "contact": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "phone": {"type": "string"},
                                    "email": {"type": "string"},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["properties"],
                "additionalProperties": False,
            },
        },
        handler=_update_deal,
        check_fn=_pipefacil_available,
        is_async=True,
        emoji="🗂️",
    )

    read_name = "pipefacil_read_profile_file"
    ctx.register_tool(
        name=read_name,
        toolset="pipefacil",
        description=(
            "Read a file from this profile's operator-approved knowledge folder or an attachment "
            "received in the current lead turn. Use path='knowledge/' to list approved reference files, "
            "then read an exact listed path; wildcards are not supported. No files from other profiles, "
            "credentials, transcripts, configuration, or plugin code are available. Read-only."
        ),
        schema={
            "name": read_name,
            "description": "Read approved reference files or current lead attachments; list only the knowledge folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Exact attachment path, knowledge/file path, or knowledge/ for a directory listing."},
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        handler=_read_profile_file,
        check_fn=_pipefacil_available,
        emoji="📄",
    )

    send_name = "pipefacil_send_messages"
    ctx.register_tool(
        name=send_name,
        toolset="pipefacil",
        description=(
            "Send one or two messages to the lead in the CURRENT Pipefacil conversation, "
            "then Hermes sends your final answer automatically. For an ordinary single text reply, "
            "write only your final answer. Use this tool to split a reply or send an "
            "approved image/document before the final text. For media, use only an exact HTTPS URL "
            "listed in this profile's SOUL.md under the Pipefacil media library. This tool has no "
            "recipient/phone parameter. You can send at most two preliminary messages total per turn, "
            "even if the tool is called more than once. A successful result means the API accepted the request, not "
            "that WhatsApp delivery was confirmed."
        ),
        schema={
            "name": send_name,
            "description": "Send preliminary split messages or approved media to the active lead. Hermes sends the final answer automatically; ordinary text replies need only a final answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["text", "image", "document"]},
                                "text": {"type": "string", "maxLength": 4000},
                                "url": {"type": "string", "maxLength": 4000},
                                "caption": {"type": "string", "maxLength": 1000},
                            },
                            "required": ["type"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["messages"],
                "additionalProperties": False,
            },
        },
        handler=_send_messages,
        check_fn=_pipefacil_available,
        is_async=True,
        emoji="📨",
    )
