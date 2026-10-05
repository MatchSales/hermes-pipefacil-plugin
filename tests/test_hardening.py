"""Deterministic contract, scope, recovery and media tests without external credentials."""

from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import hmac
import json
import os
import time

import httpx
import pytest

from test_gateway_sessions import _modules


def module(name):
    return __import__(_modules()[0].__package__ + "." + name, fromlist=["*"])


def signed(body, secret="literal-secret", timestamp=None, *, next_header=False):
    timestamp = str(int(time.time() * 1000)) if timestamp is None else str(timestamp)
    signature = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {
        "X-PipeFacil-Timestamp": timestamp,
        "X-PipeFacil-Signature-256-Next" if next_header else "X-PipeFacil-Signature-256": signature,
    }


@pytest.mark.parametrize("secret", ["245234534", "abcdef0123456789" * 4, "secret=with=equals", "ação🔑"])
@pytest.mark.parametrize("compression", ["plain", "gzip", "already_decompressed"])
@pytest.mark.parametrize("rotation", [False, True])
def test_java_hmac_exact_utf8_bytes_before_gzip_and_rotation(secret, compression, rotation):
    security = module("security")
    body = '{ "type":"message.received", "ação":"Olá 🎙️" }'.encode()
    raw = gzip.compress(body) if compression == "gzip" else body
    decoded = security.decode_body(raw, "gzip" if compression != "plain" else "")
    security.verify(
        decoded, signed(body, secret, next_header=rotation), ("old-secret", secret) if rotation else (secret,)
    )
    assert decoded == body
    assert security.parse_json(decoded)["ação"] == "Olá 🎙️"


@pytest.mark.parametrize(
    "change",
    [
        "signature_prefix",
        "hex_decoded_secret",
        "compressed_bytes",
        "json_reserialized",
        "tampered",
        "missing",
        "old",
        "future",
        "timestamp_dot",
        "secret_prefix",
    ],
)
def test_authentication_rejects_wrong_contract_and_replays(change):
    security = module("security")
    body = b'{ "n":1 }'
    secret = "1234abcdef" * 6 + "1234"
    headers = signed(body, secret)
    secrets = (secret,)
    if change == "signature_prefix":
        headers["X-PipeFacil-Signature-256"] = headers["X-PipeFacil-Signature-256"][7:]
    elif change == "hex_decoded_secret":
        value = headers["X-PipeFacil-Timestamp"].encode() + b"." + body
        headers["X-PipeFacil-Signature-256"] = (
            "sha256=" + hmac.new(bytes.fromhex(secret), value, hashlib.sha256).hexdigest()
        )
    elif change == "compressed_bytes":
        headers = signed(gzip.compress(body), secret)
    elif change == "json_reserialized":
        headers = signed(json.dumps(json.loads(body)).encode(), secret)
    elif change == "tampered":
        body = b'{ "n":2 }'
    elif change == "missing":
        headers = {}
    elif change in {"old", "future"}:
        headers = signed(body, secret, int((time.time() + (-301 if change == "old" else 301)) * 1000))
    elif change == "timestamp_dot":
        headers["X-PipeFacil-Timestamp"] += "="
    else:
        secrets = ("sha256=" + secret,)
    with pytest.raises(security.AdmissionError) as exc:
        security.verify(body, headers, secrets)
    assert exc.value.status == 401


@pytest.mark.parametrize(
    "raw,encoding,status",
    [
        (b"x", "br", 415),
        (b"x" * 1048577, "", 413),
        (b"\x1f\x8binvalid", "gzip", 400),
        (gzip.compress(b"x" * 4194305), "gzip", 413),
        (gzip.compress(b"ok")[:-2], "gzip", 400),
        (gzip.compress(b"ok") + b"trailing", "gzip", 400),
        (gzip.compress(b"a") + gzip.compress(b"b"), "gzip", 400),
    ],
)
def test_body_limits_and_gzip_validation(raw, encoding, status):
    security = module("security")
    with pytest.raises(security.AdmissionError) as exc:
        security.decode_body(raw, encoding)
    assert exc.value.status == status


@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"\xff", b"{"])
def test_json_is_strict(body):
    security = module("security")
    with pytest.raises(security.AdmissionError):
        security.parse_json(body)


def kwargs(identity="one", chat="channel:phone", body="hello"):
    return {
        "chat_id": chat,
        "phone": "phone",
        "contact": {"phone": "phone"},
        "channel": {"id": "channel"},
        "payload": {},
        "messages": [{"id": identity, "body": body, "type": "text", "timestamp": 1800000000}],
    }


def test_atomic_queue_capacity_rollback_order_conflict_and_profile_scope(tmp_path):
    state = module("state")
    a = state.State(tmp_path / "a", capacity=2, per_chat=2)
    first = a.admit(kwargs(), now=1800000000, max_age=300)
    second = a.admit(kwargs("two"), now=1800000000, max_age=300)
    assert first < second
    assert a.admit(kwargs(), now=1800000000, max_age=300) is None
    with pytest.raises(state.Conflict):
        a.admit(kwargs(body="changed"), now=1800000000, max_age=300)
    with pytest.raises(state.Full):
        a.admit(kwargs("three"), now=1800000000, max_age=300)
    with a.db() as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 2
    b = state.State(tmp_path / "b")
    assert b.admit(kwargs(), now=1800000000, max_age=300)
    assert a.path.stat().st_mode & 0o777 == 0o600
    assert a.directory.stat().st_mode & 0o777 == 0o700


def test_receipt_and_job_are_committed_once_across_concurrent_requests(tmp_path):
    state = module("state").State(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: state.admit(kwargs(), now=time.time(), max_age=300), range(16)))
    assert len([j for j in jobs if j is not None]) == 1
    assert state.status()["jobs"] == {"queued": 1}


def test_recover_only_unstarted_jobs_and_never_repeat_ambiguous_effect(tmp_path):
    s = module("state")
    state = s.State(tmp_path)
    now = time.time()

    def admit(identity):
        value = kwargs(identity)
        value["messages"][0]["timestamp"] = now
        return state.admit(value, now=now, max_age=300)

    started, queued = admit("one"), admit("two")
    assert state.next("channel:phone")[0] == started
    action, saved = state.claim_action(started, "send", {"text": "hello"})
    assert saved is None
    assert state.recover() == ["channel:phone"]
    with pytest.raises(s.StateError, match="uncertain"):
        state.claim_action(started, "send", {"text": "hello"})
    assert state.next("channel:phone")[0] == queued
    assert state.status()["jobs"] == {"interrupted": 1, "processing": 1}


def test_action_success_is_reused_and_lease_is_exclusive(tmp_path):
    s = module("state")
    a, b = s.State(tmp_path), s.State(tmp_path)
    a.acquire()
    with pytest.raises(s.StateError, match="another_gateway"):
        b.acquire()
    now = time.time()
    value = kwargs()
    value["messages"][0]["timestamp"] = now
    job = a.admit(value, now=now, max_age=300)
    a.next(value["chat_id"])
    key, _ = a.claim_action(job, "send", {"id": "file"})
    a.finish_action(key, {"message_id": "accepted"})
    assert b.claim_action(job, "send", {"id": "file"})[1] == {"message_id": "accepted"}
    a.close()
    b.acquire()
    b.close()


def test_local_library_rejects_links_invalid_content_and_cross_profile_ids(tmp_path):
    library = module("library")
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "media").mkdir(parents=True)
    (b / "media").mkdir(parents=True)
    (a / "media" / "catalog.pdf").write_bytes(b"%PDF-1.7\nprivate-profile-a")
    (b / "media" / "catalog.pdf").write_bytes(b"%PDF-1.7\nprivate-profile-b")
    secret = a / ".env"
    secret.write_text("secret")
    (a / "media" / "secret.txt").symlink_to(secret)
    os.link(secret, a / "media" / "hardlink.txt")
    asset = library.catalog(a)[0]
    assert asset.public()["filename"] == "catalog.pdf" and "path" not in asset.public()
    assert len(library.catalog(a)) == 1
    assert library.read_asset(a, asset.id, "document")[1].endswith(b"profile-a")
    for home, identity, kind in [(b, asset.id, "document"), (a, "../../.env", "document"), (a, asset.id, "image")]:
        with pytest.raises(ValueError):
            library.read_asset(home, identity, kind)
    (a / "media" / "catalog.pdf").write_bytes(b"not a pdf")
    with pytest.raises(ValueError, match="invalid_media_content"):
        library.read_asset(a, asset.id, "document")


@pytest.mark.parametrize(
    "mime,prefix",
    [
        ("audio/ogg", b"OggS"),
        ("audio/opus", b"OggS"),
        ("audio/wav", b"RIFF1234WAVE"),
        ("audio/flac", b"fLaC"),
        ("audio/x-flac", b"fLaC"),
        ("audio/m4a", b"1234ftypM4A "),
        ("audio/mp4a-latm", b"1234ftypM4A "),
        ("audio/mp4", b"1234ftypM4A "),
        ("audio/mpeg", b"ID3"),
        ("audio/aac", b"\xff\xf1abc"),
        ("audio/webm", b"\x1a\x45\xdf\xa3"),
    ],
)
def test_audio_mime_and_magic_validation(mime, prefix):
    media = module("media")
    assert media._kind_for_mime(mime) == "audio"
    assert media._valid_file_prefix(prefix, mime)
    assert not media._valid_file_prefix(b"<html>error</html>", mime)
    spec = media._media_spec(
        {
            "type": "audio",
            "media": {"mimeType": mime, "filename": "voice", "downloadUrl": "https://storage.example.org/file"},
        }
    )
    assert spec[1] == "audio"


@pytest.mark.parametrize("status,body", [(302, {"data": {}}), (200, {}), (200, []), (200, None), (200, {"data": None})])
def test_api_rejects_redirect_or_invalid_envelope(status, body):
    api = module("api")
    with pytest.raises(api.PipefacilAPIError):
        api._decode_response(httpx.Response(status, json=body))


@pytest.mark.parametrize(
    "url",
    [
        "https://crm.example.org/api/v1",
        "https://u:p@crm.example.org",
        "https://crm.example.org?x=1",
        "http://crm.example.org",
    ],
)
def test_api_requires_operator_origin_not_path_credentials_or_remote_plaintext(url):
    api = module("api")
    with pytest.raises(api.PipefacilAPIError):
        api.normalize_api_base_url(url)


def test_dns_rebinding_is_blocked_and_public_address_is_pinned(monkeypatch):
    network = module("network")
    monkeypatch.setattr(network.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(network.httpcore.ConnectError, match="not_public"):
        network.PublicBackend().connect_tcp("public.example.org", 443)
    called = []
    monkeypatch.setattr(
        network.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 443))]
    )
    monkeypatch.setattr(network.httpcore.SyncBackend, "connect_tcp", lambda self, *a: called.append(a))
    network.PublicBackend().connect_tcp("public.example.org", 443, timeout=3)
    assert called[0][0] == "93.184.216.34"


def test_cache_retention_preserves_current_files_and_removes_only_old_owned_files(tmp_path):
    media = module("media")
    root = tmp_path / "cache" / "pipefacil" / "inbound"
    root.mkdir(parents=True)
    old, current = root / "old.pdf", root / "current.pdf"
    old.write_bytes(b"old")
    current.write_bytes(b"current")
    os.utime(old, (1, 1))
    os.utime(current, (1, 1))
    media.clean_cache(tmp_path, protected=[str(current)], now=1800000000)
    assert not old.exists() and current.exists()


def test_profile_file_id_preparation_and_text_limits(tmp_path):
    tools = _modules()[1]
    library = module("library")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "catalog.pdf").write_bytes(b"%PDF-1.7")
    identity = library.catalog(tmp_path)[0].id
    assert tools._prepare_outbound_message({"type": "document", "fileId": identity}, tmp_path) == {
        "type": "document",
        "fileId": identity,
    }
    for item in [
        {"type": "image", "fileId": identity},
        {"type": "document", "fileId": identity, "url": "https://x.org/a"},
        {"type": "text", "text": "x" * 4001},
        {"type": "text", "text": "hi", "to": "other"},
    ]:
        with pytest.raises(ValueError):
            tools._prepare_outbound_message(item, tmp_path)


def test_journal_reconciliation_requires_lease_receipt_and_preserves_audit(tmp_path):
    s = module("state")
    state = s.State(tmp_path)
    value = kwargs()
    now = time.time()
    value["messages"][0]["timestamp"] = now
    job = state.admit(value, now=now, max_age=300)
    state.next(value["chat_id"])
    key, _ = state.claim_action(job, "send", {"text": "hello"})
    state.finish_action(key)
    state.finish(job, "failed")
    with pytest.raises(s.StateError, match="lease"):
        state.reconcile(key, "accepted", {"message_id": "verified"}, "External receipt")
    state.acquire()
    with pytest.raises(s.StateError, match="receipt"):
        state.reconcile(key, "accepted", {}, "External receipt")
    state.reconcile(key, "accepted", {"message_id": "verified"}, "CRM history verified by operator")
    assert state.claim_action(job, "send", {"text": "hello"})[1] == {"message_id": "verified"}
    with state.db() as db:
        assert db.execute("SELECT count(*) FROM reconciliations").fetchone()[0] == 1
    state.close()


def test_journal_retention_removes_payload_but_keeps_uncertain_reference(tmp_path):
    s = module("state")
    state = s.State(tmp_path)
    now = time.time()
    value = kwargs()
    value["messages"][0]["timestamp"] = now
    job = state.admit(value, now=now, max_age=300)
    state.next(value["chat_id"])
    key, _ = state.claim_action(job, "send", {"text": "hello"})
    state.finish_action(key)
    state.finish(job, "failed")
    state.upload("cache-key", {"key": "storage-key"})
    state.prune(now=now + 8 * 86400)
    with state.db() as db:
        row = db.execute("SELECT payload,chat FROM jobs WHERE id=?", (job,)).fetchone()
        assert dict(row) == {"payload": "{}", "chat": "retained-audit-reference"}
        assert db.execute("SELECT state FROM actions WHERE key=?", (key,)).fetchone()[0] == "uncertain"
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0


def test_cli_refuses_missing_profile_without_creating_it(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    cli = Path(module("state").__file__).with_name("state_cli.py")
    home = tmp_path / "missing"
    r = subprocess.run([sys.executable, str(cli), "--profile", str(home), "status"], capture_output=True, text=True)
    assert r.returncode == 1 and "existing Pipefacil journal" in r.stderr
    assert not home.exists()


def test_hermes_startup_probe_uses_credentials_without_overwriting_yaml(tmp_path, monkeypatch):
    adapter = _modules()[0]
    monkeypatch.setattr(
        adapter,
        "get_scoped_secret",
        lambda name, default="": {"PIPEFACIL_API_KEY": "configured", "PIPEFACIL_WEBHOOK_SECRET": "245234534"}.get(
            name, default
        ),
    )
    from gateway.config import PlatformConfig

    assert adapter.is_connected(PlatformConfig())  # Runs before the adapter exists.
    assert adapter._env_enablement() == {}  # Native Hermes merges nonempty seeds over YAML.
    assert adapter.check_requirements()
    monkeypatch.setattr(adapter, "get_scoped_secret", lambda name, default="": default)
    assert not adapter.is_connected(PlatformConfig())


def test_ambiguous_send_blocks_changed_native_fallback_in_same_turn(tmp_path):
    s = module("state")
    state = s.State(tmp_path)
    now = time.time()
    value = kwargs()
    value["messages"][0]["timestamp"] = now
    job = state.admit(value, now=now, max_age=300)
    state.next(value["chat_id"])
    key, _ = state.claim_action(job, "send", {"text": "**hello**"})
    state.finish_action(key)
    with pytest.raises(s.StateError, match="prior_send_uncertain"):
        state.claim_action(job, "send", {"text": "hello"})
    state.finish(job, "failed")
    value["messages"][0]["id"] = "new-event"
    another = state.admit(value, now=now, max_age=300)
    state.next(value["chat_id"])
    assert state.claim_action(another, "send", {"text": "hello"})[1] is None


def test_signed_pipefacil_ingress_delegates_customer_authorization_to_backend():
    assert _modules()[0].PipefacilAdapter.authorization_is_upstream.fget(None) is True


def test_signature_vector_from_actual_compiled_java_backend(monkeypatch):
    # Produced by AiAgentWebhookSignatureUtil in the unmodified CRM target/classes.
    body = '{ "type":"message.received", "texto":"Olá 🎙️" }'.encode()
    headers = {'X-PipeFacil-Timestamp': '1791162000000', 'X-PipeFacil-Signature-256':
               'sha256=2b52e2245fede24332d449ad4ab545d23cde4a576fff4609376b0a22f64c5d6e'}
    security = module('security')
    monkeypatch.setattr(security.time, 'time', lambda: 1791162000)
    for raw, encoding in [(body, ''), (gzip.compress(body), 'gzip')]:
        security.verify(security.decode_body(raw, encoding), headers, ('245234534',))
