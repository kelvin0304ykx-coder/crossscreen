"""Publish the public manifest's verified assets with GitHub Actions' repository token."""
from concurrent.futures import ThreadPoolExecutor
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
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-(?:beta\.[0-9]+|dev[0-9]+)", tag):
        raise ValueError("Invalid release tag")
    target = Path("release-assets")
    target.mkdir(exist_ok=True)
    tagged_revision = manifest.get("source_commit", revision)
    if not re.fullmatch(r"[0-9a-f]{40}", tagged_revision):
        raise ValueError("Invalid source commit")

    def fetch(entry):
        name = entry["name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise ValueError("Invalid asset manifest")
        url = f"https://www.sibaihuo.com/crossscreen/downloads/{tag[1:]}/{name}"
        path = target / name
        with urllib.request.urlopen(url, timeout=180) as response, path.open("wb") as output:
            shutil.copyfileobj(response, output)
        if path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Asset checksum mismatch: " + name)
        return str(path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        assets = list(pool.map(fetch, manifest["assets"]))
    with urllib.request.urlopen(source + "RELEASE_NOTES.md", timeout=30) as response:
        Path("release-notes.md").write_bytes(response.read())
    exists = subprocess.run(["gh", "release", "view", tag, "--repo", repository], capture_output=True).returncode == 0
    cleanup = manifest.get("cleanup_superseded", [])
    if cleanup:
        if not exists or not isinstance(cleanup, list) or any(
                not isinstance(old, str) or old == tag or
                not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+", old)
                for old in cleanup):
            raise ValueError("Invalid superseded release cleanup request")
        # Cleanup is a separate publication request after public download checks.
        current = json.loads(subprocess.check_output(
            ["gh", "api", f"repos/{repository}/releases/tags/{tag}"], text=True))
        expected = {entry["name"]: entry["bytes"] for entry in manifest["assets"]}
        if current["draft"] or {a["name"]: a["size"] for a in current["assets"]} != expected:
            raise RuntimeError("Replacement release is not ready")
        for old in cleanup:
            present = subprocess.run(["gh", "release", "view", old, "--repo", repository], capture_output=True)
            if present.returncode == 0:
                subprocess.run(["gh", "release", "delete", old, "--repo", repository, "--yes"], check=True)
                print("Deleted superseded release", old)
        return
    if exists:
        raise RuntimeError(f"Release {tag} already exists")
    else:
        subprocess.run(["gh", "release", "create", tag, *assets, "--repo", repository, "--target", tagged_revision,
                        "--prerelease", "--latest=false", "--title", f"跨屏 CrossScreen {tag[1:]}",
                        "--notes-file", "release-notes.md"], check=True)


if __name__ == "__main__":
    main()
