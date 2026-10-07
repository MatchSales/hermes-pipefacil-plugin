"""Upload approved commercial media to the plugin's private R2 gateway."""

import hashlib
import json
import re
from urllib.parse import quote

import httpx

from .api import PipefacilAPIError, normalize_api_base_url
from .library import MAX_BYTES, MIMES
from .network import PublicTransport


def validate_receipt(data, *, base_url, filename, mime_type, body):
    origin = normalize_api_base_url(base_url)
    # Loopback HTTP is permitted for local integration tests only. Delivery URLs
    # must still satisfy the existing CRM's HTTPS contract.
    origin = origin.replace("http://", "https://", 1)
    if (not isinstance(data, dict) or not isinstance(data.get("key"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", data["key"])
            or data.get("url") != f"{origin}/media/{data['key']}"
            or data.get("filename") != filename or data.get("contentType") != mime_type
            or data.get("sizeBytes") != len(body)
            or data.get("sha256") != hashlib.sha256(body).hexdigest()):
        raise PipefacilAPIError("R2 returned an invalid persistent media receipt.")
    return data


def upload_message_media(*, token, base_url, filename, mime_type, body):
    """Separate storage credentials from the CRM key; never follow redirects."""
    if not base_url or not token:
        raise PipefacilAPIError(
            "Configure PIPEFACIL_MEDIA_BASE_URL and PIPEFACIL_MEDIA_UPLOAD_TOKEN in this profile.",
            definite_rejection=True)
    origin = normalize_api_base_url(base_url)
    if mime_type not in MIMES or not 0 < len(body) <= MAX_BYTES:
        raise PipefacilAPIError("Invalid commercial media size or MIME.", definite_rejection=True)
    transport = PublicTransport() if origin.startswith("https://") else None
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False, trust_env=False, transport=transport) as client:
            with client.stream("POST", origin + "/upload", content=body, headers={
                "Authorization": "Bearer " + token,
                "Content-Type": mime_type,
                "X-Media-Filename": quote(filename, safe=""),
                "Accept": "application/json",
                "User-Agent": "hermes-pipefacil-plugin/0.5.1",
            }) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes(8192):
                    size += len(chunk)
                    if size > 64 * 1024:
                        raise PipefacilAPIError("R2 receipt exceeded the 64 KiB limit.")
                    chunks.append(chunk)
                if not 200 <= response.status_code < 300:
                    raise PipefacilAPIError("R2 media upload returned HTTP " + str(response.status_code) + ".",
                                            status_code=response.status_code)
                try:
                    payload = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError):
                    payload = None
    except httpx.HTTPError:
        raise PipefacilAPIError("Could not reach the R2 media service (network or timeout error).") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    return validate_receipt(data, base_url=origin, filename=filename, mime_type=mime_type, body=body)
