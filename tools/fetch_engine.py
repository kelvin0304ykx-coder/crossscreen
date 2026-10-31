"""Fetch the pinned, unmodified engine and validate it before extraction."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "tools/engine-manifest.json").read_text(encoding="utf-8"))
    platform = "macos-arm64.dmg" if sys.platform == "darwin" else "win-x64-portable.zip"
    if sys.platform not in ("win32", "darwin"):
        raise SystemExit("Build packages are currently available for Windows and macOS.")
    entry = next(item for item in manifest if item["name"].endswith(platform))
    cache = ROOT / "vendor/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / entry["name"]
    if not archive.exists():
        with urllib.request.urlopen(entry["url"], timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != entry["sha256"]:
        raise SystemExit("Engine checksum mismatch. Remove the downloaded archive and try again.")
    if sys.platform == "win32":
        destination = ROOT / "vendor/windows"
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                target = (destination / info.filename).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise SystemExit("Archive contains an invalid path.")
            package.extractall(destination)
    else:
        destination = ROOT / "vendor/macos/Deskflow.app"
        if destination.exists():
            print("Engine already extracted.")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="crossscreen-engine-") as mount:
            subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, str(archive)],
                           input="Y\n", text=True, check=True)
            try:
                subprocess.run(["ditto", str(Path(mount) / "Deskflow.app"), str(destination)], check=True)
            finally:
                subprocess.run(["hdiutil", "detach", mount], check=True)
    print("Engine SHA-256 verified and extracted.")


if __name__ == "__main__":
    main()
