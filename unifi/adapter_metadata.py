"""Build current Core UI metadata with small selective-client overlays."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    """Read metadata from the installed version of Home Assistant."""
    return json.loads(path.read_text(encoding="utf-8"))


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge recursively; null removes only an explicitly obsolete UI key."""
    result = deepcopy(base)
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            original = result.get(key)
            result[key] = _merge(original if isinstance(original, dict) else {}, value)
        else:
            result[key] = value
    return result


def _write_json(path: Path, data: dict[str, Any]) -> bool:
    """Replace only the adapter cache, never the built-in source files."""
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == serialized:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as file:
        temporary = Path(file.name)
        try:
            file.write(serialized)
            file.flush()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)
    return True


def build_metadata(native_dir: Path, adapter_dir: Path) -> list[Path]:
    """Refresh metadata before HA caches translations for this component.

    Source definitions always come from the currently installed Core, including
    previously unknown sensor/service labels and languages. Only explicit custom
    labels and removed tracking controls are kept in the adapter's overlays.
    """
    native_dir, adapter_dir = Path(native_dir), Path(adapter_dir)
    if native_dir.resolve() == adapter_dir.resolve():
        raise ValueError("The metadata cache must not overwrite built-in UniFi")
    overlay_dir = adapter_dir / "overlays"
    strings_overlay = _read_json(overlay_dir / "strings.json")
    russian_overlay = _read_json(overlay_dir / "ru.json")
    icon_overlay = _read_json(overlay_dir / "icons.json")
    native_strings = _read_json(native_dir / "strings.json")
    native_english_path = native_dir / "translations" / "en.json"
    native_english = (
        _read_json(native_english_path)
        if native_english_path.is_file()
        else native_strings
    )
    outputs: dict[Path, dict[str, Any]] = {
        adapter_dir / "strings.json": _merge(native_strings, strings_overlay),
        adapter_dir / "icons.json": _merge(
            _read_json(native_dir / "icons.json"), icon_overlay
        ),
    }
    languages = {
        path.stem: path for path in (native_dir / "translations").glob("*.json")
    }
    languages.setdefault("en", native_english_path)
    languages.setdefault("ru", native_dir / "translations" / "ru.json")
    for language, source in languages.items():
        translated = _read_json(source) if source.is_file() else {}
        data = _merge(native_english, translated)
        data = _merge(data, strings_overlay)
        if language == "ru":
            data = _merge(data, russian_overlay)
        outputs[adapter_dir / "translations" / f"{language}.json"] = data
    return [path for path, data in outputs.items() if _write_json(path, data)]
