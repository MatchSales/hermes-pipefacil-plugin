"""The AI-agent Java contract: HMAC(timestamp + '.' + uncompressed JSON, literal secret)."""

import hashlib
import hmac
import json
import re
import time
import zlib

MAX_COMPRESSED = 1_048_576
MAX_BODY = 4_194_304


class AdmissionError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.status = status


def decode_body(raw, encoding):
    encoding = encoding.strip().lower()
    if encoding not in {"", "identity", "gzip"}:
        raise AdmissionError("unsupported_encoding", 415)
    if len(raw) > MAX_BODY:
        raise AdmissionError("payload_too_large", 413)
    if encoding == "gzip" and raw.startswith(b"\x1f\x8b"):
        if len(raw) > MAX_COMPRESSED:
            raise AdmissionError("payload_too_large", 413)
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = decoder.decompress(raw, MAX_BODY + 1)
            if len(body) > MAX_BODY or decoder.unconsumed_tail:
                raise AdmissionError("payload_too_large", 413)
            if not decoder.eof or decoder.unused_data:
                raise ValueError()
            return body
        except AdmissionError:
            raise
        except (ValueError, zlib.error):
            raise AdmissionError("invalid_gzip") from None
    # Hermes' shared listener may have decompressed the body already. HMAC still
    # authenticates these exact bytes; never JSON-serialize or decompress twice.
    if encoding != "gzip" and len(raw) > MAX_COMPRESSED:
        raise AdmissionError("payload_too_large", 413)
    return raw


def verify(body, headers, secrets, *, now=None):
    timestamp = headers.get("X-PipeFacil-Timestamp", "")
    if not re.fullmatch(r"[0-9]{1,16}", timestamp):
        raise AdmissionError("invalid_timestamp", 401)
    if abs((time.time() if now is None else now) - int(timestamp) / 1000) > 300:
        raise AdmissionError("expired_signature", 401)
    if not secrets or not any(secrets):
        raise AdmissionError("webhook_secret_missing", 503)
    signatures = [headers.get(name, "") for name in (
        "X-PipeFacil-Signature-256", "X-PipeFacil-Signature-256-Next")]
    message = timestamp.encode("ascii") + b"." + body
    accepted = False
    for secret in secrets:
        if secret:
            expected = "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
            for signature in signatures:
                if re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
                    accepted |= hmac.compare_digest(expected, signature)
    if not accepted:
        raise AdmissionError("invalid_signature", 401)


def parse_json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    try:
        return json.loads(body, object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise AdmissionError("invalid_json") from None


ADMIN_REPLY = "Configurações e administração do agente estão disponíveis apenas ao responsável pelo sistema."
ADMIN = re.compile(r"^\s*/|(?:altere?|mud[ae]|troqu[ae]|edit[ae]|reinici[ae]|restart|change|update|delete|apague)"
                   r".{0,70}(?:SOUL\.md|config\.yaml|\.env\b|gateway|perfil do agente|modelo do agente|system prompt)", re.I | re.S)


def public_text(text, secrets=()):
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("invalid_public_text")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[credencial removida]")
    text = re.sub(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{16,})",
                  "[credencial removida]", text, flags=re.I)
    text.encode("utf-8")
    return text
