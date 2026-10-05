"""Public Pipefacil tools must use current turn facts and profile-only reads."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _tools_module(monkeypatch, profile_home: Path, *, deal_seq=42, media_paths=frozenset()):
    gateway = ModuleType("gateway")
    gateway.__path__ = []
    platforms = ModuleType("gateway.platforms")
    platforms.__path__ = []
    shared = ModuleType("gateway.platforms._shared")
    shared.get_scoped_secret = lambda *args: "test-key"
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: str(profile_home)
    host_tools = ModuleType("tools")
    host_tools.__path__ = []
    registry = ModuleType("tools.registry")
    registry.tool_error = lambda message, **kwargs: {"error": message}
    registry.tool_result = lambda value: value
    file_tools = ModuleType("tools.file_tools")
    file_tools.read_file_tool = lambda path, **kwargs: {"read": path, **kwargs}
    for name, module in (
        ("gateway", gateway), ("gateway.platforms", platforms),
        ("gateway.platforms._shared", shared), ("hermes_constants", constants),
        ("tools", host_tools), ("tools.registry", registry), ("tools.file_tools", file_tools),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    package_name = "pipefacil_security_test"
    spec = importlib.util.spec_from_file_location(
        package_name, PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, package_name, package)
    spec.loader.exec_module(package)
    adapter_module = ModuleType(f"{package_name}.adapter")
    adapter_module.adapter_for_profile = lambda home, chat: SimpleNamespace(
        trusted_turn_context=lambda current_chat: {
            "deal_seq": deal_seq, "media_paths": media_paths,
        }, crm=SimpleNamespace(update=lambda context, key, properties, active: {
            "success": True, "seq": context["deal_seq"], "properties": properties})
    )
    monkeypatch.setitem(sys.modules, f"{package_name}.adapter", adapter_module)
    module = __import__(f"{package_name}.tools", fromlist=["*"])
    monkeypatch.setattr(module, "_active_pipefacil_chat", lambda session, home: ("chat:lead", ""))
    return module


def test_update_uses_current_deal_seq_not_model_supplied_value(tmp_path, monkeypatch):
    module = _tools_module(monkeypatch, tmp_path, deal_seq=42)
    monkeypatch.setattr(module, "_api_base_url", lambda: "https://example.test")

    result = asyncio.run(module._update_deal(
        {"seq": 999, "properties": {"notes": "confirmed"}}, session_id="session-1",
    ))

    assert result["success"] is True
    assert result["seq"] == 42
    assert result["properties"] == {"notes": "confirmed"}


def test_update_refuses_when_current_event_has_no_deal(tmp_path, monkeypatch):
    module = _tools_module(monkeypatch, tmp_path, deal_seq=None)
    assert "error" in asyncio.run(module._update_deal(
        {"properties": {"notes": "test"}}, session_id="session-1",
    ))


def test_read_is_limited_to_profile_knowledge_and_current_attachment(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    knowledge = profile / "knowledge"
    knowledge.mkdir(parents=True)
    document = knowledge / "guide.txt"
    document.write_text("approved", encoding="utf-8")
    attachment = profile / "cache" / "pipefacil" / "inbound" / "current.pdf"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"%PDF-test")
    other = tmp_path / "other.txt"
    other.write_text("outside", encoding="utf-8")
    profile_secret = profile / ".env"
    profile_secret.write_text("secret", encoding="utf-8")
    module = _tools_module(monkeypatch, profile, media_paths=frozenset({str(attachment)}))

    assert module._read_profile_file({"path": "knowledge/guide.txt"}, session_id="s")["read"] == str(document)
    assert module._read_profile_file({"path": str(attachment)}, session_id="s")["read"] == str(attachment)
    assert "error" in module._read_profile_file({"path": str(other)}, session_id="s")
    assert "error" in module._read_profile_file({"path": str(profile_secret)}, session_id="s")
    link = knowledge / "outside.txt"
    link.symlink_to(other)
    assert "error" in module._read_profile_file({"path": str(link)}, session_id="s")
