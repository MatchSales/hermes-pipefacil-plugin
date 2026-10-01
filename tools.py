"""CRM update tool used by Pipefacil-triggered Hermes conversations."""

from __future__ import annotations

import asyncio
from typing import Any

from gateway.platforms._shared import get_scoped_secret

from .api import (
    DEFAULT_API_BASE_URL,
    PipefacilAPIError,
    normalize_api_base_url,
    update_deal,
)

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


async def _update_deal(args: dict[str, Any], **kwargs: Any) -> str:
    from tools.registry import tool_error, tool_result

    try:
        seq = int(args.get("seq"))
    except (TypeError, ValueError):
        return tool_error("Provide the deal seq from the trusted Pipefacil event context.")
    if seq <= 0:
        return tool_error("The deal seq must be a positive integer.")
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


def register_tools(ctx) -> None:
    name = "pipefacil_update_deal"
    ctx.register_tool(
        name=name,
        toolset="pipefacil",
        description=(
            "Update fields of the lead/deal from the authenticated Pipefacil webhook. Use only the seq "
            "provided in the trusted event context, and only when the conversation gives reliable evidence "
            "for the change. For a stage move pass its exact stageId; a lost stage also requires lostReason. "
            "Never use this to mark a deal won or lost based only on a promise or an inference."
        ),
        schema={
            "name": name,
            "description": "Partially update one Pipefacil deal in this profile's workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seq": {"type": "integer", "minimum": 1},
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
                "required": ["seq", "properties"],
                "additionalProperties": False,
            },
        },
        handler=_update_deal,
        check_fn=_pipefacil_available,
        is_async=True,
        emoji="🗂️",
    )
