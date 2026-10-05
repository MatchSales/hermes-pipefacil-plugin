"""Ensure compatibility jobs load the Hermes source they claim to validate."""

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def verify_selected_hermes_source():
    expected = os.environ.get("HERMES_SOURCE")
    if expected:
        root = Path(expected).resolve()
        for name in ("gateway.platforms.base", "hermes_cli.plugins", "tools.tool_search_catalog"):
            path = Path(importlib.import_module(name).__file__).resolve()
            assert path.is_relative_to(root), f"Wrong Hermes source for {name}: {path}, expected {root}"
