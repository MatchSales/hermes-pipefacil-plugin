"""Small, profile-scoped client for Pipefacil's public API."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx


DEFAULT_API_BASE_URL = "https://pipefacil-server.matchsales.com.br"
API_TIMEOUT_SECONDS = 20.0


class PipefacilAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def normalize_api_base_url(value: str | None) -> str:
    base_url = (value or DEFAULT_API_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(base_url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme != "https" and not local_http
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise PipefacilAPIError(
            "api_base_url must be an HTTPS origin (HTTP is allowed for localhost only)."
        )
    return base_url


def _decode_response(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    if response.is_error:
        code = payload.get("code") or payload.get("error")
        # Avoid returning full upstream payloads: they can contain lead PII or internal details.
        if response.status_code == 403:
            message = "Pipefacil rejected the request. Check API_ACCESS/ADVANCED_API and the key permissions."
        elif response.status_code == 401:
            message = "Pipefacil rejected the API key. Check PIPEFACIL_API_KEY for this profile."
        elif response.status_code == 429:
            message = "Pipefacil rate limit reached. Retry after a short delay."
        elif code:
            message = f"Pipefacil API returned {response.status_code} ({str(code)[:80]})."
        else:
            message = f"Pipefacil API returned HTTP {response.status_code}."
        raise PipefacilAPIError(message, status_code=response.status_code)
    return payload


def request_json(
    *,
    api_key: str,
    base_url: str,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not api_key.strip():
        raise PipefacilAPIError("PIPEFACIL_API_KEY is not configured for this profile.")
    query = f"?{urlencode(params, doseq=True)}" if params else ""
    url = f"{normalize_api_base_url(base_url)}{path}{query}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key.strip()}",
        "User-Agent": "hermes-pipefacil-plugin/0.1.0",
    }
    try:
        with httpx.Client(timeout=API_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = client.request(method, url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise PipefacilAPIError("Could not reach the Pipefacil API (network or timeout error).") from exc
    return _decode_response(response)


def fetch_conversation_history(
    *,
    api_key: str,
    base_url: str,
    conversation_key: str,
    channel_id: str | None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], bool]:
    if not conversation_key.strip():
        raise PipefacilAPIError("A contact phone or conversation id is required to load history.")
    limit = max(1, min(int(limit), 200))
    path = f"/api/v1/conversations/{quote(conversation_key.strip(), safe='')}/messages"
    params: dict[str, Any] = {"limit": limit}
    if channel_id:
        params["channelId"] = channel_id
    envelope = request_json(
        api_key=api_key,
        base_url=base_url,
        method="GET",
        path=path,
        params=params,
    )
    data = envelope.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise PipefacilAPIError("Pipefacil returned an invalid conversation history response.")
    items = [item for item in data["items"] if isinstance(item, dict)]
    return items, bool(data.get("hasMore"))


def send_text_message(
    *,
    api_key: str,
    base_url: str,
    recipient: str,
    text: str,
    channel_id: str | None = None,
    sender_phone_number_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"to": recipient, "type": "text", "text": text}
    if channel_id:
        body["channelId"] = channel_id
    elif sender_phone_number_id:
        body["senderPhoneNumberId"] = sender_phone_number_id
    return request_json(
        api_key=api_key,
        base_url=base_url,
        method="POST",
        path="/api/v1/conversations/messages",
        payload=body,
    )


def update_deal(
    *, api_key: str, base_url: str, seq: int, properties: dict[str, Any]
) -> dict[str, Any]:
    return request_json(
        api_key=api_key,
        base_url=base_url,
        method="PATCH",
        path=f"/api/v1/deals/{int(seq)}",
        payload=properties,
    )
