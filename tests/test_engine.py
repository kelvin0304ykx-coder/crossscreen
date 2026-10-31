import os
from pathlib import Path
import subprocess
import sys
import time


def test_worker_stops_child_when_its_owner_disappears(tmp_path):
    from crossscreen.worker import worker_main
    heartbeat = tmp_path / "heartbeat"
    child = tmp_path / "child.py"
    child.write_text("import pathlib,time\np=pathlib.Path(" + repr(str(heartbeat)) + ")\n"
                     "while True:\n p.write_text(str(time.monotonic()))\n time.sleep(.05)\n")
    proc = subprocess.Popen([sys.executable, "-m", "crossscreen.worker", sys.executable, str(child)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert heartbeat.exists(), "child did not start"
        proc.stdin.close()
        assert proc.wait(timeout=6) == 0
        first = heartbeat.read_text()
        time.sleep(.2)
        assert heartbeat.read_text() == first, "child survived owner EOF"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_engine_log_does_not_forward_keystrokes_or_unrecognized_lines():
    from crossscreen.engine import status_from_line
    assert status_from_line('DEBUG: key down secret-value') is None
    assert status_from_line('INFO: ordinary input text') is None
    assert status_from_line('INFO: client "mac" has connected') == ("connected", "两台电脑已连接")
    assert status_from_line('INFO: switch from "windows" to "mac" at 12,34') == ("mac", "正在控制 Mac")
    assert status_from_line('INFO: switch from "mac" to "windows" at 12,34') == ("windows", "正在控制 Windows")
    assert status_from_line('ERROR: failed to verify server certificate fingerprint')[0] == "error"


def test_mac_runtime_permission_revocation_has_a_specific_recovery_message():
    from crossscreen.engine import status_from_line
    state, message = status_from_line('CRITICAL: process is not trusted anymore, quitting')
    assert state == "permission"
    assert "辅助功能" in message


def test_existing_ipc_engine_is_preserved_and_new_start_is_refused():
    import uuid
    import pytest
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from crossscreen.engine import ensure_engine_not_running
    app = QCoreApplication.instance() or QCoreApplication([])
    name = "crossscreen-test-" + uuid.uuid4().hex
    server = QLocalServer()
    assert server.listen(name)
    try:
        with pytest.raises(ValueError, match="共享组件"):
            ensure_engine_not_running(name)
        assert server.isListening()
        client = QLocalSocket()
        client.connectToServer(name)
        assert client.waitForConnected(200)
        client.abort()
    finally:
        server.close()


def test_stop_timeout_never_reports_input_has_returned_to_local():
    from PySide6.QtCore import QCoreApplication, QProcess
    from crossscreen.engine import Engine
    app = QCoreApplication.instance() or QCoreApplication([])

    class UnstoppableProcess:
        def state(self): return QProcess.Running
        def closeWriteChannel(self): pass
        def waitForFinished(self, _): return False
        def terminate(self): pass

    engine = Engine()
    engine.process = UnstoppableProcess()
    states, running = [], []
    engine.status.connect(lambda state, text: states.append(state))
    engine.running_changed.connect(running.append)
    assert engine.stop() is False
    assert "stopped" not in states
    assert states[-1] == "error"
    assert running[-1] is True
