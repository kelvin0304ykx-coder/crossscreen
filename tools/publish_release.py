"""Publish the public manifest's verified assets with GitHub Actions' repository token."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    revision = os.environ["GITHUB_SHA"]
    source = f"https://raw.githubusercontent.com/{repository}/{revision}/"
    with urllib.request.urlopen(source + "release-manifest.json", timeout=30) as response:
        manifest = json.load(response)
    tag = manifest["tag"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+", tag):
        raise ValueError("Invalid release tag")
    target = Path("release-assets")
    target.mkdir(exist_ok=True)
    assets = []
    for entry in manifest["assets"]:
        name = entry["name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise ValueError("Invalid asset manifest")
        url = f"https://sibaihuo.com/crossscreen/downloads/{tag[1:]}/{name}"
        path = target / name
        with urllib.request.urlopen(url, timeout=180) as response, path.open("wb") as output:
            shutil.copyfileobj(response, output)
        if path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Asset checksum mismatch: " + name)
        assets.append(str(path))
    with urllib.request.urlopen(source + "RELEASE_NOTES.md", timeout=30) as response:
        Path("release-notes.md").write_bytes(response.read())
    exists = subprocess.run(["gh", "release", "view", tag, "--repo", repository], capture_output=True).returncode == 0
    if exists:
        raise RuntimeError(f"Release {tag} already exists")
    else:
        subprocess.run(["gh", "release", "create", tag, *assets, "--repo", repository, "--target", revision,
                        "--prerelease", "--latest=false", "--title", f"跨屏 CrossScreen {tag[1:]}",
                        "--notes-file", "release-notes.md"], check=True)


if __name__ == "__main__":
    main()
