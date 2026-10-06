"""Versioned profile-owned extensions, resolved through Hermes' scoped plugin hooks."""

import inspect
from pathlib import Path
import re

API_REVISION = 1
DESCRIBE_EVENT = "pipefacil.extension.describe.v1"
PUBLIC_EVENT = "pipefacil.extension.api.v1"
_CALLBACKS = frozenset(
    {"connect", "disconnect", "before_event", "reset", "prepare_event", "complete", "before_send", "format_text"}
)


class ExtensionError(RuntimeError):
    pass


class Extensions:
    def __init__(self, home, names=()):
        self.home = Path(home).resolve()
        if (
            not isinstance(names, (list, tuple))
            or len(names) > 8
            or any(not isinstance(n, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", n) for n in names)
            or len(set(names)) != len(names)
        ):
            raise ValueError("extension_plugins must be a list of distinct plugin IDs.")
        self.names = tuple(names)
        self.descriptors = None

    def resolve(self):
        if self.descriptors is not None:
            return self.descriptors
        if not self.names:
            self.descriptors = ()
            return self.descriptors
        from hermes_cli.plugins import get_plugin_manager, invoke_hook
        from gateway.run import _profile_runtime_scope

        with _profile_runtime_scope(self.home):
            manager = get_plugin_manager()
            responses = invoke_hook("gateway_platform_event", event_type=DESCRIBE_EVENT, profile_home=str(self.home))
            loaded = {p["name"] for p in manager.list_plugins() if p["enabled"] and not p["error"]}
        result = []
        for name in self.names:
            matches = [r for r in responses if isinstance(r, dict) and r.get("name") == name]
            if name not in loaded or len(matches) != 1:
                raise ExtensionError("Required Pipefacil extension is missing, disabled or failed: " + name)
            descriptor = matches[0]
            if (
                type(descriptor.get("api_revision")) is not int
                or descriptor["api_revision"] != API_REVISION
                or not isinstance(descriptor.get("profile_home"), str)
                or Path(descriptor["profile_home"]).resolve() != self.home
            ):
                raise ExtensionError("Extension contract or profile mismatch: " + name)
            for field in ("tools", "toolsets", "disabled_tools"):
                values = descriptor.get(field, [])
                if not isinstance(values, (tuple, list)) or any(
                    not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", v) for v in values
                ):
                    raise ExtensionError("Invalid extension " + field)
            callbacks = descriptor.get("callbacks", {})
            if (
                not isinstance(callbacks, dict)
                or set(callbacks) - _CALLBACKS
                or not all(callable(v) for v in callbacks.values())
            ):
                raise ExtensionError("Invalid extension callbacks: " + name)
            result.append(descriptor)
        self.descriptors = tuple(result)
        return self.descriptors

    def tool_names(self, base):
        descriptors = self.resolve()
        disabled = {n for d in descriptors for n in d.get("disabled_tools", [])}
        tools = {n for d in descriptors for n in d.get("tools", [])}
        if tools & set(base) or disabled - set(base):
            raise ExtensionError("Extension must not replace common tools or disable unknown tools.")
        return (set(base) | tools) - disabled

    def toolsets(self):
        return list(dict.fromkeys(n for d in self.resolve() for n in d.get("toolsets", [])))

    def metadata(self):
        return [{"name": d["name"], "version": d.get("version"), "api_revision": API_REVISION} for d in self.resolve()]

    async def call(self, hook, **kwargs):
        results = []
        for descriptor in self.resolve():
            callback = descriptor.get("callbacks", {}).get(hook)
            if callback:
                result = callback(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                results.append(result)
        return results

    def before_send(self, **kwargs):
        for descriptor in self.resolve():
            callback = descriptor.get("callbacks", {}).get("before_send")
            if callback:
                try:
                    result = callback(**kwargs)
                except Exception as exc:
                    raise ExtensionError("Profile delivery guard failed: " + type(exc).__name__) from None
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise ExtensionError("before_send must be synchronous inside the serialized effect.")
                if result is False:
                    raise ExtensionError("Profile extension suppressed this reply.")

    def format_text(self, text, **kwargs):
        for descriptor in self.resolve():
            callback = descriptor.get("callbacks", {}).get("format_text")
            if callback:
                try:
                    text = callback(text=text, **kwargs)
                except Exception as exc:
                    raise ExtensionError("Profile formatter failed: " + type(exc).__name__) from None
                if not isinstance(text, str):
                    if inspect.iscoroutine(text):
                        text.close()
                    raise ExtensionError("Extension formatter must return text.")
        return text
