"""Resolve baseline and latest stable HA releases for scheduled compatibility CI."""

import argparse
import json
import re
from urllib.request import urlopen

BASELINE = "2026.11.0"
PYPI_JSON = "https://pypi.org/pypi/homeassistant/{version}/json"


def metadata(version: str = "") -> dict:
    """Read public package metadata from the official Python package index."""
    url = PYPI_JSON.format(version=version).replace("//json", "/json")
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def stable_releases(package: dict) -> list[str]:
    """Ignore pre-releases, empty releases and entirely yanked releases."""
    return sorted(
        (
            version
            for version, files in package["releases"].items()
            if re.fullmatch(r"\d+\.\d+\.\d+", version)
            and any(not file.get("yanked", False) for file in files)
        ),
        key=lambda version: tuple(map(int, version.split("."))),
    )


def python_minor(requires_python: str) -> str:
    """Use the release's Python minor instead of hardcoding one for future HA."""
    if match := re.search(r">=\s*(\d+\.\d+)(?:\.\d+)?", requires_python):
        return match.group(1)
    raise ValueError(f"Unsupported Python requirement: {requires_python!r}")


def main() -> None:
    """Print one compact JSON matrix for GitHub Actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-version", default="")
    args = parser.parse_args()
    if args.ha_version:
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.ha_version):
            parser.error("--ha-version must be an exact stable X.Y.Z release")
        versions = [args.ha_version]
    else:
        versions = list(dict.fromkeys([BASELINE, stable_releases(metadata())[-1]]))
    include = []
    for version in versions:
        package = metadata(version)
        include.append(
            {
                "ha": version,
                "python": python_minor(package["info"]["requires_python"]),
            }
        )
    print(json.dumps({"include": include}, separators=(",", ":")))


if __name__ == "__main__":
    main()
