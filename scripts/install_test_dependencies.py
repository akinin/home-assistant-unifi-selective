"""Install matching HA fixtures and the current builtin UniFi requirements.

Run with the Python executable of an isolated test virtual environment. No
integration dependency version is copied into this repository.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def install(requirements: list[str]) -> None:
    """Install packages only into the interpreter running this script."""
    if not requirements:
        return
    if uv := shutil.which("uv"):
        command = [uv, "pip", "install", "--python", sys.executable]
    else:
        command = [sys.executable, "-m", "pip", "install"]
    subprocess.run([*command, *requirements], check=True)


def builtin_requirements(domain: str = "unifi") -> list[str]:
    """Read requirements and declared integration dependencies from installed HA."""
    spec = importlib.util.find_spec("homeassistant")
    if spec is None or spec.origin is None:
        raise RuntimeError("Install Home Assistant before reading builtin requirements")
    components = Path(spec.origin).parent / "components"
    visited: set[str] = set()
    requirements: list[str] = []

    def collect(name: str) -> None:
        if name in visited:
            return
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"Invalid builtin integration name: {name!r}")
        visited.add(name)
        manifest = json.loads((components / name / "manifest.json").read_text())
        for dependency in manifest.get("dependencies", []):
            collect(dependency)
        requirements.extend(manifest.get("requirements", []))

    collect(domain)
    return list(dict.fromkeys(requirements))


def main() -> None:
    """Prepare a specified Core release or the already installed development base."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-version", help="Exact stable Home Assistant version")
    args = parser.parse_args()
    if args.ha_version:
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.ha_version):
            parser.error("--ha-version must be an exact stable X.Y.Z release")
        install(
            [
                f"homeassistant=={args.ha_version}",
                f"pytest-homeassistant=={args.ha_version}",
            ]
        )
    requirements = builtin_requirements()
    print("Builtin UniFi dependencies:", ", ".join(requirements), flush=True)
    install(requirements)


if __name__ == "__main__":
    main()
