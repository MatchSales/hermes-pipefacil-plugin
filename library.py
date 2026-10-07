"""Local profile media catalog. Models select IDs, never filesystem paths or upload credentials."""

from dataclasses import dataclass
from contextlib import ExitStack
import hashlib
import mimetypes
import os
from pathlib import Path
import stat
from urllib.parse import unquote, urlsplit

import httpx

from .media import _valid_file_prefix, normalize_mime, resolve_media_link
from .network import PublicTransport

MAX_BYTES = 16 * 1024 * 1024  # Existing public CRM upload contract.
MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf",
                  "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  "text/csv", "text/plain"})


@dataclass(frozen=True)
class RemoteUpload:
    filename: str
    mime: str


def read_remote_asset(home, url, kind, filename=None, mime=None):
    """Copy only this profile's approved HTTPS image/document, with pinned public DNS."""
    if resolve_media_link(home, kind=kind, url=url) is None:
        raise ValueError("media_not_in_profile")
    with httpx.Client(timeout=25.0, follow_redirects=False, trust_env=False, transport=PublicTransport()) as client:
        with client.stream("GET", url) as response:
            if not 200 <= response.status_code < 300:
                raise ValueError(f"Approved media download returned HTTP {response.status_code}.")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or not 0 < int(length) <= MAX_BYTES):
                raise ValueError("invalid_media_size")
            actual_mime = normalize_mime(response.headers.get("Content-Type"))
            if actual_mime in {"", "application/octet-stream", "binary/octet-stream"}:
                actual_mime = normalize_mime(mime)
            if actual_mime not in MIMES or ("image" if actual_mime.startswith("image/") else "document") != kind:
                raise ValueError("invalid_media_type")
            chunks, size = [], 0
            for chunk in response.iter_bytes(64 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("invalid_media_size")
                chunks.append(chunk)
            body = b"".join(chunks)
            if not body or not _valid_file_prefix(body[:64], actual_mime):
                raise ValueError("invalid_media_content")
    name = Path(str(filename or unquote(urlsplit(url).path))).name
    name = "".join(c for c in name if c.isprintable() and c not in '\\"')[:200] or "media.bin"
    return RemoteUpload(name, actual_mime), body, hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class Asset:
    id: str
    label: str
    filename: str
    kind: str
    mime: str
    size: int
    path: Path

    def public(self):
        return {"id": self.id, "label": self.label, "filename": self.filename,
                "type": self.kind, "mimeType": self.mime, "size": self.size}


def catalog(home):
    home = Path(home).resolve()
    root = home / "media"
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ValueError("invalid_media_directory")
    result = []
    scanned = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(directory) / d).is_symlink())
        for filename in sorted(files):
            scanned += 1
            if scanned > 1000:
                raise ValueError("media_catalog_too_large")
            if filename.startswith("."):
                continue
            path = Path(directory) / filename
            info = path.lstat()
            mime = mimetypes.guess_type(filename)[0]
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or mime not in MIMES
                    or not 0 < info.st_size <= MAX_BYTES):
                continue
            relative = path.relative_to(root).as_posix()
            identity = hashlib.sha256((str(home) + ":" + relative).encode()).hexdigest()[:32]
            result.append(Asset(identity, str(Path(relative).with_suffix("")), filename,
                                "image" if mime.startswith("image/") else "document", mime, info.st_size, path))
            if len(result) == 200:
                return result
    return result


def read_asset(home, identity, kind):
    asset = next((a for a in catalog(home) if a.id == identity and a.kind == kind), None)
    if asset is None:
        raise ValueError("media_not_in_profile")
    root = Path(home).resolve() / "media"
    if not asset.path.resolve(strict=True).is_relative_to(root):
        raise ValueError("media_outside_profile")
    for parent in asset.path.parents:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError("media_outside_profile")
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
        descriptor = os.open(Path(home).resolve(), flags)
        stack.callback(os.close, descriptor)
        parts = asset.path.relative_to(Path(home).resolve()).parts
        for part in parts[:-1]:
            descriptor = os.open(part, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        stream = stack.enter_context(os.fdopen(fd, "rb"))
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= MAX_BYTES:
            raise ValueError("invalid_media_file")
        body = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if len(body) > MAX_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("media_changed_during_read")
    if not _valid_file_prefix(body[:64], asset.mime):
        raise ValueError("invalid_media_content")
    return asset, body, hashlib.sha256(body).hexdigest()
