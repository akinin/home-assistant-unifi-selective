"""Build a deployable adapter plus metadata generated from installed Core."""

import argparse
import hashlib
import importlib.util
from pathlib import Path
import runpy
import shutil
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.find_spec("homeassistant")
    if spec is None or spec.origin is None:
        raise SystemExit(
            "Install the target Home Assistant and its native requirements first"
        )
    native = Path(spec.origin).parent / "components" / "unifi"
    build_metadata = runpy.run_path(str(root / "unifi/adapter_metadata.py"))[
        "build_metadata"
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory) / "unifi"
        stage.mkdir()
        for path in (root / "unifi").glob("*.py"):
            shutil.copyfile(path, stage / path.name)
        shutil.copyfile(root / "unifi/manifest.json", stage / "manifest.json")
        shutil.copytree(root / "unifi/overlays", stage / "overlays")
        build_metadata(native, stage)
        if (native / "services.yaml").is_file():
            shutil.copyfile(native / "services.yaml", stage / "services.yaml")
        files = sorted(path for path in stage.rglob("*") if path.is_file())
        with tarfile.open(args.output, "w:gz") as archive:
            for path in files:
                archive.add(
                    path, arcname=str(path.relative_to(stage.parent)), recursive=False
                )
        checksums = args.output.with_suffix(args.output.suffix + ".sha256")
        checksums.write_text(
            "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(stage.parent)}\n"
                for path in files
            )
        )
        print(f"Built {args.output} ({len(files)} files; backend: {native})")
    print(f"SHA256 {hashlib.sha256(args.output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
