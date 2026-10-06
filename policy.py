"""Runtime tool restriction for public lead conversations, including tool_call wrappers."""

NAMES = frozenset({"pipefacil_update_deal", "pipefacil_current_deal", "pipefacil_read_profile_file",
                   "pipefacil_list_media", "pipefacil_send_messages", "pipefacil_handoff"})


def pre_tool_call(tool_name, args=None, **kwargs):
    from gateway.session_context import get_session_env
    from .adapter import _ACTIVE_PIPEFACIL_TURN

    if _ACTIVE_PIPEFACIL_TURN.get() is None and get_session_env("HERMES_SESSION_PLATFORM") != "pipefacil":
        return None
    if tool_name in NAMES | {"tool_search", "tool_describe"}:
        return None
    if tool_name == "tool_call":
        try:
            from tools.tool_search_validation import normalize_tool_call_entries
            calls, error = normalize_tool_call_entries(args or {})
            if not error and len(calls) == 1 and calls[0]["name"] in NAMES:
                return None
            if not error and len(calls) > 1 and all(call["name"] in NAMES for call in calls):
                import json
                first = json.dumps({"calls": [calls[0]]}, ensure_ascii=False)
                return {"action": "block", "message": "Hermes exige uma ferramenta local por chamada. Execute primeiro tool_call com " + first[:2000] + "; depois execute as demais separadamente."}
        except (ImportError, ValueError, TypeError, KeyError):
            pass
    return {"action": "block", "message": "Ferramenta indisponível no atendimento público. Use uma ferramenta Pipefacil por chamada."}
