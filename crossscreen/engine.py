from pathlib import Path
import os
import re
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from .config import AppConfig, write_engine_settings
from .security import ensure_identity, trust_peer


def find_engine() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    candidates = [base / "engine" / "deskflow-core.exe", base / "engine" / "deskflow-core",
                  base / "engine" / "Deskflow.app" / "Contents" / "MacOS" / "deskflow-core"]
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parents[1] / "Resources/engine/Deskflow.app/Contents/MacOS/deskflow-core")
    project = Path(__file__).resolve().parents[1]
    candidates += [project / "vendor/windows/deskflow-1.27.0-win-x64-portable/deskflow-core.exe",
                   project / "vendor/macos/Deskflow.app/Contents/MacOS/deskflow-core"]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("未找到共享组件，请使用完整的程序文件夹。")


def status_from_line(line: str):
    # Only controlled status text leaves this parser. No input text or raw
    # debug lines are forwarded to the interface or written to disk.
    if "DEBUG:" in line or "VERBOSE:" in line:
        return None
    if 'switch from ' in line:
        match = re.search(r'switch from "[^"]+" to "([^"]+)"', line)
        if match:
            is_mac = match[1].lower() == "mac"
            return ("mac" if is_mac else "windows", "正在控制 Mac" if is_mac else "正在控制 Windows")
    lowered = line.lower()
    if "has connected" in lowered or "connected to server" in lowered:
        return "connected", "两台电脑已连接"
    if "fingerprint" in lowered and any(x in lowered for x in ("failed", "does not match")):
        return "error", "设备证书不匹配，请重新交换两端配对信息。"
    if "accessibility" in lowered or "assistive" in lowered or "not trusted anymore" in lowered:
        return "permission", "请在 Mac 系统设置中允许辅助功能访问。"
    if "address already in use" in lowered or "cannot bind" in lowered:
        return "error", "连接端口被占用，请停止其他共享程序或更换端口。"
    if "unrecognised" in lowered or "unknown computer name" in lowered:
        return "error", "两端设备名称不匹配，请重新配对。"
    if "disconnected" in lowered or "connection timed out" in lowered:
        return "waiting", "连接已断开，等待重新连接"
    if "started server" in lowered:
        return "waiting", "Windows 已就绪，等待 Mac 连接"
    if "cannot connect" in lowered or "connection refused" in lowered:
        return "waiting", "正在等待另一台电脑，请检查地址与网络。"
    if "FATAL:" in line or "CRITICAL:" in line:
        return "error", "共享组件启动失败，请检查网络、系统权限和设备配对。"
    return None


def ensure_engine_not_running(name="Deskflow-core"):
    probe = QLocalSocket()
    probe.connectToServer(name)
    present = probe.waitForConnected(150)
    probe.abort()
    if present:
        raise ValueError("已有键鼠共享组件正在运行，请先停止另一个跨屏或 Deskflow 程序。")


class Engine(QObject):
    status = Signal(str, str)
    running_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.ipc = QLocalSocket(self)
        self.ipc.readyRead.connect(self._read_ipc)
        self.ipc.connected.connect(lambda: self.ipc.write(b"hello=1.27.0\n"))
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._connect_ipc)
        self.buffer = ""
        self.ipc_buffer = ""
        self.stopping = False
        self.config = None
        self.last_problem = None

    @property
    def running(self):
        return self.process.state() != QProcess.NotRunning

    def start(self, config: AppConfig, root: Path):
        if self.running:
            raise ValueError("请先停止当前共享。")
        config.validate()
        if not config.peer_fingerprint:
            raise ValueError("请先导入另一台电脑的配对信息。")
        ensure_engine_not_running()
        engine = find_engine()
        ensure_identity(root)
        peer_role = "client" if config.role == "server" else "server"
        trust_peer(root, config.peer_fingerprint, peer_role)
        settings = write_engine_settings(config, root)
        env = QProcessEnvironment.systemEnvironment()
        for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM"):
            env.remove(name)
        env.insert("XDG_CONFIG_HOME", str(Path(root) / "initial"))
        env.insert("XDG_STATE_HOME", str(Path(root) / "state"))
        env.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(env)
        if getattr(sys, "frozen", False):
            worker_args = ["--engine-worker"]
        else:
            worker_args = [str(Path(__file__).resolve().parents[1] / "main.py"), "--engine-worker"]
        worker_args += [str(engine), config.role, "-s", str(settings)]
        self.config = config
        self.last_problem = None
        self.stopping = False
        self.buffer = ""
        self.ipc_buffer = ""
        self.process.start(sys.executable, worker_args)
        self.running_changed.emit(True)
        self.status.emit("starting", "正在启动共享连接")
        self.timer.start()

    def stop(self):
        self.stopping = True
        self.timer.stop()
        self.ipc.abort()
        if self.running:
            self.process.closeWriteChannel()
            if not self.process.waitForFinished(4500):
                # The worker handles SIGTERM and always tears down its child.
                self.process.terminate()
                if not self.process.waitForFinished(3000):
                    self.status.emit("error", "共享组件尚未退出，请稍后再次停止；暂时不要关闭程序。")
                    self.running_changed.emit(True)
                    return False
        self.status.emit("stopped", "共享已停止，键鼠恢复本机")
        self.running_changed.emit(False)
        return True

    def _connect_ipc(self):
        if self.running and self.ipc.state() == QLocalSocket.UnconnectedState:
            self.ipc.connectToServer("Deskflow-core")

    def _read_ipc(self):
        self.ipc_buffer += bytes(self.ipc.readAll()).decode("utf-8", "replace")
        while "\n" in self.ipc_buffer:
            line, self.ipc_buffer = self.ipc_buffer.split("\n", 1)
            command, _, arg = line.partition("=")
            if command == "versionMismatch" and re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", arg):
                self.ipc.write(f"hello={arg}\n".encode())
            elif command == "connectionState":
                if arg.lower() == "connected":
                    self.status.emit("connected", "两台电脑已连接，鼠标跨屏即可使用")
                elif arg.lower() in ("connecting", "listening", "disconnected"):
                    self.status.emit("waiting", "等待另一台电脑连接")
            elif command == "connectedClients":
                if arg:
                    self.status.emit("connected", "两台电脑已连接，鼠标跨屏即可使用")
                else:
                    self.status.emit("waiting", "等待 Mac 连接")
            elif command == "connectionRefused":
                self.status.emit("error", "对端拒绝连接，请检查配对、设备名和系统权限。")

    def _read_output(self):
        self.buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            update = status_from_line(line)
            if update:
                if update[0] in ("permission", "error"):
                    self.last_problem = update
                self.status.emit(*update)
        self.buffer = self.buffer[-4096:]

    def _error(self, _):
        if not self.stopping:
            self.status.emit("error", "共享组件未能启动，请检查程序文件是否完整。")

    def _finished(self, code, _):
        self.timer.stop()
        self.ipc.abort()
        self.running_changed.emit(False)
        if not self.stopping:
            self.status.emit(*(self.last_problem or ("error", f"共享连接已结束（{code}），可检查权限后重新开始。")))
