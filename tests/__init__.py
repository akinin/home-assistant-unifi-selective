"""Expose the shipped integration in the Home Assistant installation namespace."""

# Mock-controller fixtures adapted from Home Assistant Core 2026.11.0
# tests/components/unifi/conftest.py (Apache-2.0).
# The repository ships unifi/ directly; expose its installation namespace for tests.
from importlib.machinery import ModuleSpec
from pathlib import Path
import sys
from types import ModuleType

_custom = ModuleType("custom_components")
_custom.__path__ = [str(Path(__file__).resolve().parents[1])]
_custom.__spec__ = ModuleSpec("custom_components", loader=None, is_package=True)
sys.modules.setdefault("custom_components", _custom)
