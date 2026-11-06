"""Current Core metadata must keep flowing through the small UI overlays."""

import json
from pathlib import Path
import shutil

from custom_components.unifi.adapter_metadata import build_metadata
import pytest


def write_json(path: Path, value: dict) -> None:
    """Create a minimal future Core metadata fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_new_core_metadata_and_languages_survive_overlay(tmp_path: Path) -> None:
    """New native sensors, options and translations need no manual copying."""
    native = tmp_path / "core"
    adapter = tmp_path / "adapter"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "unifi" / "overlays",
        adapter / "overlays",
    )
    strings = {
        "entity": {"sensor": {"future_sensor": {"name": "Future sensor"}}},
        "options": {"step": {"init": {"data": {"future_setting": "New Core setting"}}}},
    }
    write_json(native / "strings.json", strings)
    write_json(native / "icons.json", {"entity": {"sensor": {"future": "mdi:new"}}})
    write_json(native / "translations" / "en.json", strings)
    write_json(
        native / "translations" / "ru.json",
        {"entity": {"sensor": {"future_sensor": {"name": "Новый датчик"}}}},
    )
    write_json(
        native / "translations" / "fr.json",
        {"entity": {"sensor": {"future_sensor": {"name": "Nouveau capteur"}}}},
    )
    originals = {path: path.read_bytes() for path in native.rglob("*.json")}
    changed = build_metadata(native, adapter)
    assert changed
    merged = json.loads((adapter / "strings.json").read_text())
    assert merged["entity"]["sensor"] == strings["entity"]["sensor"]
    assert merged["options"]["step"]["init"]["data"]["future_setting"] == (
        "New Core setting"
    )
    ru = json.loads((adapter / "translations" / "ru.json").read_text())
    assert ru["entity"]["sensor"]["future_sensor"]["name"] == "Новый датчик"
    assert ru["entity"]["switch"]["block_client"]["name"] == "Блокировка доступа"
    fr = json.loads((adapter / "translations" / "fr.json").read_text())
    assert fr["entity"]["sensor"]["future_sensor"]["name"] == "Nouveau capteur"
    icons = json.loads((adapter / "icons.json").read_text())
    assert icons["entity"]["sensor"]["future"] == "mdi:new"
    assert icons["entity"]["switch"]["block_client"]["default"] == "mdi:ethernet-off"
    assert {path: path.read_bytes() for path in originals} == originals
    assert build_metadata(native, adapter) == []


def test_metadata_cannot_replace_native_source(tmp_path: Path) -> None:
    """A misconfigured destination never writes into the installed Core package."""
    with pytest.raises(ValueError, match="must not overwrite"):
        build_metadata(tmp_path, tmp_path)
