import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows bundle smoke")
def test_windows_bundle_opens_and_closes_without_installed_python(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = root / "dist/CrossScreen/CrossScreen.exe"
    if not binary.exists():
        pytest.skip("bundle has not been built")
    screenshot = tmp_path / "window.png"
    env = os.environ.copy()
    env["CROSSSCREEN_DATA"] = str(tmp_path / "profile")
    proc = subprocess.Popen([str(binary), "--screenshot", str(screenshot)], env=env)
    try:
        try:
            code = proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pytest.fail("packaged app failed to open and close; possible startup dialog")
        assert code == 0
        assert screenshot.stat().st_size > 10000
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows bundle worker smoke")
def test_frozen_worker_preserves_pipes_and_stops_child(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = root / "dist/CrossScreen/CrossScreen.exe"
    if not binary.exists():
        pytest.skip("bundle has not been built")
    heartbeat = tmp_path / "heartbeat"
    child = tmp_path / "child.py"
    child.write_text("import pathlib,time\np=pathlib.Path(" + repr(str(heartbeat)) + ")\n"
                     "while True:\n p.write_text(str(time.monotonic()))\n time.sleep(.05)\n")
    proc = subprocess.Popen([str(binary), "--engine-worker", sys.executable, str(child)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert heartbeat.exists(), "frozen worker did not keep its engine running"
        first = heartbeat.read_text()
        time.sleep(.2)
        assert heartbeat.read_text() != first
        proc.stdin.close()
        assert proc.wait(timeout=6) == 0
        first = heartbeat.read_text()
        time.sleep(.2)
        assert heartbeat.read_text() == first
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
