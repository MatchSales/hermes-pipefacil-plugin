"""Profile-scoped media allowlists and bounded Pipefacil webhook downloads."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import mimetypes
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .network import PublicTransport


MAX_INBOUND_MEDIA_BYTES = 25 * 1024 * 1024
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 25.0
INBOUND_CACHE_RETENTION_SECONDS = 24 * 3600
_AUDIO_MIMES = {"audio/ogg", "audio/opus", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a",
                "audio/aac", "audio/wav", "audio/x-wav", "audio/flac", "audio/webm"}
_MEDIA_LIBRARY_LINE = re.compile(
    r"^\s*-\s*label:\s*(?P<label>[^|]+?)\s*\|\s*type:\s*"
    r"(?P<kind>image|document)\s*\|\s*url:\s*(?P<url>\S+)\s*$",
    re.IGNORECASE,
)
_DOCUMENT_MIMES = {
    "application/pdf",
    "application/rtf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/epub+zip",
    "text/plain",
    "text/csv",
    "text/markdown",
}
_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


@dataclass(frozen=True)
class MediaLibraryItem:
    label: str
    kind: str
    url: str


@dataclass(frozen=True)
class InboundMediaResult:
    path: str | None = None
    mime_type: str = ""
    filename: str = ""
    error: str = ""


def _https_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and "." in parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and not parsed.fragment
        )
    except ValueError:
        return False


def parse_media_library(soul_text: str) -> list[MediaLibraryItem]:
    """Read only explicit HTTPS entries from one profile's SOUL.md."""
    items = []
    for line in soul_text.splitlines():
        match = _MEDIA_LIBRARY_LINE.fullmatch(line)
        if not match:
            continue
        url = match.group("url").strip()
        if not _https_url(url):
            continue
        items.append(
            MediaLibraryItem(
                label=match.group("label").strip(),
                kind=match.group("kind").lower(),
                url=url,
            )
        )
    return items


def load_media_library(profile_home: Path) -> list[MediaLibraryItem]:
    soul_path = Path(profile_home) / "SOUL.md"
    try:
        soul = soul_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return parse_media_library(soul)


def resolve_media_link(profile_home: Path, *, kind: str, url: str) -> MediaLibraryItem | None:
    """Exact URL and type match; never inherit another profile's SOUL.md entries."""
    if not _https_url(url):
        return None
    return next(
        (item for item in load_media_library(profile_home) if item.kind == kind and item.url == url),
        None,
    )


def _safe_download_url(value: Any) -> bool:
    if not _https_url(value):
        return False
    try:
        host = urlsplit(value).hostname or ""
        # Signed webhook URLs are expected to point at public object storage. Reject literal
        # private/loopback addresses and local hostnames before opening an outbound connection.
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return not host.endswith((".localhost", ".local", ".internal")) and host != "localhost"
        return address.is_global
    except ValueError:
        return False


def _media_spec(message: dict[str, Any]) -> tuple[str, str, str, str] | InboundMediaResult | None:
    media = message.get("media")
    message_kind = str(message.get("type") or "").strip().lower()
    if not isinstance(media, dict):
        if message_kind not in {"", "text", "chat", "message"}:
            return InboundMediaResult(
                error=(
                    "The current webhook message has no downloadable media metadata."
                    if message_kind in {"image", "photo", "document", "file"}
                    else f"The current webhook attachment type '{message_kind}' is unsupported."
                )
            )
        return None

    download_url = media.get("downloadUrl") or media.get("download_url")
    filename = str(media.get("filename") or "").strip()
    declared_mime = str(media.get("mimeType") or media.get("mime_type") or "").split(";", 1)[0].strip().lower()
    if not download_url:
        return InboundMediaResult(error="The current webhook media has no temporary download URL.")
    if not _safe_download_url(download_url):
        return InboundMediaResult(error="The current webhook media URL is invalid or not HTTPS.")

    if not declared_mime and filename:
        declared_mime = mimetypes.guess_type(filename)[0] or ""
    if declared_mime.startswith("image/") and declared_mime != "image/svg+xml":
        kind = "image"
    elif declared_mime in _AUDIO_MIMES:
        kind = "audio"
    elif declared_mime in _DOCUMENT_MIMES:
        kind = "document"
    elif message_kind in {"image", "photo"}:
        return InboundMediaResult(error="The current webhook image type is missing or unsupported.")
    elif message_kind in {"document", "file"}:
        return InboundMediaResult(error="The current webhook document type is missing or unsupported.")
    else:
        return InboundMediaResult(error="The current webhook media type is unsupported.")
    return str(download_url), kind, declared_mime, filename


def _clean_filename(value: str, mime_type: str) -> str:
    filename = value.replace("\\", "/").split("/")[-1]
    filename = "".join(char for char in filename if char >= " " and char not in "/\\")
    filename = filename.strip(" .")[:180]
    if not filename:
        filename = f"attachment{mimetypes.guess_extension(mime_type) or '.bin'}"
    return filename


def _kind_for_mime(mime_type: str) -> str | None:
    if mime_type.startswith("image/") and mime_type != "image/svg+xml":
        return "image"
    if mime_type in _DOCUMENT_MIMES:
        return "document"
    if mime_type in _AUDIO_MIMES:
        return "audio"
    return None


def _valid_file_prefix(content: bytes, mime_type: str) -> bool:
    if mime_type in _AUDIO_MIMES:
        if mime_type in {"audio/ogg", "audio/opus"}:
            return content.startswith(b"OggS")
        if mime_type in {"audio/wav", "audio/x-wav"}:
            return content.startswith(b"RIFF") and content[8:12] == b"WAVE"
        if mime_type == "audio/flac":
            return content.startswith(b"fLaC")
        if mime_type in {"audio/mp4", "audio/x-m4a"}:
            return content[4:8] == b"ftyp"
        if mime_type == "audio/webm":
            return content.startswith(b"\x1a\x45\xdf\xa3")
        return content.startswith(b"ID3") or (len(content) > 2 and content[0] == 255 and content[1] & 224 == 224)
    if mime_type == "application/pdf":
        return content.startswith(b"%PDF-")
    if mime_type.startswith("image/"):
        if mime_type == "image/webp":
            return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        if mime_type == "image/avif":
            return len(content) >= 12 and content[4:8] == b"ftyp" and b"avif" in content[:32]
        return any(content.startswith(signature) and mime_type == expected for signature, expected in _IMAGE_SIGNATURES)
    if mime_type.startswith("application/vnd.openxmlformats-officedocument") or mime_type in {
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/epub+zip",
    }:
        return content.startswith(b"PK\x03\x04")
    if mime_type in {"application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"}:
        return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    return bool(content.strip())


def _download_to_profile_cache(
    *, url: str, kind: str, declared_mime: str, filename: str, profile_home: Path, message_key: str,
) -> InboundMediaResult:
    directory = Path(profile_home) / "cache" / "pipefacil" / "inbound"
    temp_path: Path | None = None
    try:
        with httpx.Client(
            timeout=MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=PublicTransport(),
        ) as client:
            with client.stream("GET", url, headers={"Accept": f"{kind}/*, application/octet-stream"}) as response:
                if not 200 <= response.status_code < 300:
                    hint = " (temporary link may have expired)" if response.status_code in {401, 403, 404, 410} else ""
                    return InboundMediaResult(error=f"media download returned HTTP {response.status_code}{hint}")
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        if int(length) > MAX_INBOUND_MEDIA_BYTES:
                            return InboundMediaResult(error="media download exceeded the 25 MiB limit")
                    except ValueError:
                        return InboundMediaResult(error="media download returned an invalid content length")
                response_mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if response_mime in {"application/octet-stream", "binary/octet-stream", ""}:
                    mime_type = declared_mime
                else:
                    mime_type = response_mime
                if _kind_for_mime(mime_type) != kind:
                    return InboundMediaResult(error="downloaded media MIME type does not match a supported attachment")

                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                if directory.is_symlink():
                    raise ValueError("invalid profile cache directory")
                directory.chmod(0o700)
                suffix = Path(_clean_filename(filename, mime_type)).suffix[:20]
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=f"{hashlib.sha256(message_key.encode()).hexdigest()[:16]}-",
                    suffix=suffix or (mimetypes.guess_extension(mime_type) or ".bin"),
                    dir=directory, delete=False,
                ) as stream:
                    temp_path = Path(stream.name)
                    size = 0
                    prefix = bytearray()
                    for chunk in response.iter_bytes(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAX_INBOUND_MEDIA_BYTES:
                            raise ValueError("media download exceeded the 25 MiB limit")
                        if len(prefix) < 64:
                            prefix.extend(chunk[:64 - len(prefix)])
                        stream.write(chunk)
                if not size or not _valid_file_prefix(bytes(prefix), mime_type):
                    raise ValueError("downloaded media is empty or does not match its MIME type")
        return InboundMediaResult(
            path=str(temp_path), mime_type=mime_type, filename=_clean_filename(filename, mime_type)
        )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        message = str(exc) if isinstance(exc, ValueError) else "network error or timeout"
        return InboundMediaResult(error=f"media could not be downloaded or validated ({message})")


async def download_inbound_media(
    message: dict[str, Any], *, profile_home: Path, message_key: str,
) -> InboundMediaResult | None:
    """Download one attachment carried by this webhook message, never a history item."""
    spec = _media_spec(message)
    if spec is None or isinstance(spec, InboundMediaResult):
        return spec
    url, kind, declared_mime, filename = spec
    return await asyncio.to_thread(
        _download_to_profile_cache,
        url=url,
        kind=kind,
        declared_mime=declared_mime,
        filename=filename,
        profile_home=profile_home,
        message_key=message_key,
    )


def clean_cache(profile_home, *, protected=(), now=None):
    directory = Path(profile_home) / "cache" / "pipefacil" / "inbound"
    if directory.is_symlink() or not directory.exists():
        return
    cutoff = (time.time() if now is None else now) - INBOUND_CACHE_RETENTION_SECONDS
    protected = frozenset(protected)
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or str(path) in protected:
            continue
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
