"""Extension isolation, fail-closed requirements, and serialized delivery contracts."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from test_hardening import module


def descriptor(home, **kwargs):
    return {
        "name": "client-business",
        "version": "1.0.0",
        "api_revision": 1,
        "profile_home": str(home),
        "tools": ["client_tool"],
        "toolsets": ["client_business"],
        "disabled_tools": ["pipefacil_update_deal"],
        "callbacks": {},
        **kwargs,
    }


def resolved(tmp_path, monkeypatch, rows, loaded=True):
    import hermes_cli.plugins as plugins

    manager = SimpleNamespace(list_plugins=lambda: [{"name": "client-business", "enabled": loaded, "error": None}])
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(plugins, "invoke_hook", lambda *args, **kwargs: rows)
    return module("extensions").Extensions(tmp_path, ["client-business"])


def test_only_configured_same_profile_tools_are_allowed(tmp_path, monkeypatch):
    ext = resolved(
        tmp_path, monkeypatch, [descriptor(tmp_path), descriptor(tmp_path, name="other-business", tools=["other_tool"])]
    )
    assert ext.tool_names({"pipefacil_handoff", "pipefacil_update_deal"}) == {"pipefacil_handoff", "client_tool"}
    assert ext.toolsets() == ["client_business"]
    assert ext.metadata() == [{"name": "client-business", "version": "1.0.0", "api_revision": 1}]


@pytest.mark.parametrize(
    "change",
    [
        {"profile_home": "/different/profile"},
        {"api_revision": 2},
        {"api_revision": True},
        {"tools": ["../unsafe"]},
        {"callbacks": {"unknown": lambda: None}},
        {"callbacks": {"connect": False}},
    ],
)
def test_wrong_profile_revision_or_descriptor_is_rejected(tmp_path, monkeypatch, change):
    with pytest.raises(module("extensions").ExtensionError):
        resolved(tmp_path, monkeypatch, [descriptor(tmp_path, **change)]).resolve()


@pytest.mark.parametrize("rows,loaded", [([], True), ("duplicate", True), ("single", False)])
def test_missing_duplicated_or_disabled_dependency_fails_closed(tmp_path, monkeypatch, rows, loaded):
    if rows == "duplicate":
        rows = [descriptor(tmp_path), descriptor(tmp_path)]
    elif rows == "single":
        rows = [descriptor(tmp_path)]
    with pytest.raises(module("extensions").ExtensionError):
        resolved(tmp_path, monkeypatch, rows, loaded).resolve()


def test_extensions_cannot_replace_transport_tools(tmp_path, monkeypatch):
    ext = resolved(tmp_path, monkeypatch, [descriptor(tmp_path, tools=["pipefacil_handoff"])])
    with pytest.raises(module("extensions").ExtensionError):
        ext.tool_names({"pipefacil_handoff", "pipefacil_update_deal"})


def test_callbacks_support_async_but_delivery_guard_is_synchronous(tmp_path, monkeypatch):
    seen = []

    async def prepare_event(event):
        seen.append(event)
        return True

    ext = resolved(
        tmp_path,
        monkeypatch,
        [descriptor(tmp_path, callbacks={"prepare_event": prepare_event, "before_send": lambda **kwargs: False})],
    )
    assert asyncio.run(ext.call("prepare_event", event="new")) == [True]
    assert seen == ["new"]
    with pytest.raises(module("extensions").ExtensionError):
        ext.before_send(message={})
    ext.descriptors[0]["callbacks"]["before_send"] = prepare_event
    with pytest.raises(module("extensions").ExtensionError):
        ext.before_send(event="new")
    assert seen == ["new"]


def test_formatter_errors_are_not_silently_skipped(tmp_path, monkeypatch):
    ext = resolved(tmp_path, monkeypatch, [descriptor(tmp_path, callbacks={"format_text": lambda **kwargs: None})])
    with pytest.raises(module("extensions").ExtensionError):
        ext.format_text("text")

    def broken(**kwargs):
        raise ValueError("private error must not be exposed")

    ext.descriptors[0]["callbacks"]["format_text"] = broken
    with pytest.raises(module("extensions").ExtensionError, match="ValueError"):
        ext.format_text("text")


def test_stored_audio_is_bound_to_an_accepted_upload_in_exact_job(tmp_path):
    state = module("state").State(tmp_path)
    adapter = SimpleNamespace(state=state, profile_home=tmp_path)
    from test_hardening import kwargs

    data = kwargs()
    job = state.admit(data, now=time.time(), max_age=300)
    state.next(data["chat_id"])
    context = {"job_id": job}
    factory = module("public").stored_audio
    with pytest.raises(ValueError):
        factory(adapter, context, {"key": "storage-key"}, "digest")
    identity, _ = state.claim_action(job, "generate_voice", {"textDigest": "digest"})
    state.finish_action(identity, {"key": "storage-key"})
    audio = factory(adapter, context, {"key": "storage-key"}, "digest")
    assert audio.profile_home == str(tmp_path) and audio.job_id == job
    with pytest.raises(ValueError):
        factory(adapter, {"job_id": job + 1}, {"key": "storage-key"}, "digest")
    with pytest.raises(ValueError):
        factory(adapter, context, {"key": "different"}, "digest")
    state.close()
