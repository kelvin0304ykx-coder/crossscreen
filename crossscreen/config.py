from dataclasses import asdict, dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".crossscreen-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("设备名只能使用英文字母、数字、横线或下划线，最长 64 个字符。")
    return name


def validate_host(host: str) -> str:
    if not isinstance(host, str) or not host or len(host) > 253:
        raise ValueError("请填写 Windows 的 IP 地址或主机名。")
    try:
        value = ipaddress.IPv4Address(host)
        if value.is_multicast or value.is_unspecified:
            raise ValueError("请填写具体的电脑地址。")
        return host
    except ipaddress.AddressValueError:
        pass
    labels = host.rstrip(".").split(".")
    if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
        raise ValueError("地址格式不正确；请单独填写 IP 或主机名，端口在旁边设置。")
    if len(labels) == 4 and all(x.isdigit() for x in labels):
        raise ValueError("IPv4 地址不正确。")
    return host


def select_lan_address(addresses) -> str:
    networks = [ipaddress.ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    for interface, ip in addresses:
        if any(x in interface.lower() for x in ("vethernet", "wsl", "vmware", "virtualbox", "docker", "tailscale")):
            continue
        try:
            address = ipaddress.ip_address(ip)
            if any(address in net for net in networks):
                return ip
        except ValueError:
            continue
    return "127.0.0.1"


def default_host() -> str:
    # Enumerate interfaces, rather than resolving the machine name: proxy
    # fake-IP DNS and tunnel interfaces can otherwise produce 198.18.0.1.
    try:
        from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
        addresses = []
        for interface in QNetworkInterface.allInterfaces():
            if not interface.flags() & QNetworkInterface.IsUp:
                continue
            for entry in interface.addressEntries():
                if entry.ip().protocol() == QAbstractSocket.IPv4Protocol:
                    addresses.append((interface.humanReadableName(), entry.ip().toString()))
        return select_lan_address(addresses)
    except (ImportError, OSError):
        pass
    return "127.0.0.1"


def app_data_dir() -> Path:
    if os.environ.get("CROSSSCREEN_DATA"):
        return Path(os.environ["CROSSSCREEN_DATA"]).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "CrossScreen"
    return Path.home() / "Library" / "Application Support" / "CrossScreen"


@dataclass(eq=True)
class AppConfig:
    role: str = "server"
    local_name: str = "windows"
    peer_name: str = "mac"
    host: str = "127.0.0.1"
    port: int = 24870
    side: str = "left"
    peer_fingerprint: str = ""
    clipboard: bool = True

    def validate(self):
        if type(self.clipboard) is not bool:
            raise ValueError("剪贴板设置无效")
        validate_name(self.local_name)
        validate_name(self.peer_name)
        validate_host(self.host)
        if self.local_name.lower() == self.peer_name.lower():
            raise ValueError("两台电脑的设备名不能相同。")
        if self.role not in ("server", "client") or self.side not in ("left", "right"):
            raise ValueError("设备角色或屏幕位置不正确。")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("端口需在 1024 至 65535 之间。")
        if self.peer_fingerprint and not re.fullmatch(r"[a-f0-9]{64}", self.peer_fingerprint):
            raise ValueError("配对指纹不正确。")
        return self

    def save(self, path: Path):
        self.validate()
        atomic_write(path, json.dumps(asdict(self), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: Path):
        obj = cls(**json.loads(Path(path).read_text(encoding="utf-8")))
        return obj.validate()


def render_layout(server: str, client: str, side: str) -> str:
    validate_name(server)
    validate_name(client)
    if server.lower() == client.lower() or side not in ("left", "right"):
        raise ValueError("屏幕布局不正确。")
    opposite = "left" if side == "right" else "right"
    return (
        f"section: links\n    {server}:\n        {side} = {client}\n"
        f"    {client}:\n        {opposite} = {server}\nend\n\n"
        f"section: options\n    keystroke(Control+Alt+Shift+F12) = switchToScreen({server})\n"
        f"    keystroke(Control+Alt+Shift+F11) = switchToScreen({client})\nend\n"
    )


def write_engine_settings(config: AppConfig, root: Path) -> Path:
    config.validate()
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    server = config.local_name if config.role == "server" else config.peer_name
    client = config.peer_name if config.role == "server" else config.local_name
    layout = root / "layout.conf"
    atomic_write(layout, render_layout(server, client, config.side))
    certificate = (root / "tls" / "identity.pem").as_posix()
    # Deskflow 1.27 reads computer definitions from its main INI, rather than
    # the legacy screens section. Both TLS trust files are relative to this INI.
    text = f"""[core]
computerName={config.local_name}
coreMode={2 if config.role == 'server' else 1}
port={config.port}
interface={config.host if config.role == 'server' else ''}
processMode=0
preventSleep=false
enableEnterCommand=false
enableExitCommand=false

[client]
remoteHost={config.host}
languageSync=false
dynamicConnectionInterval=true

[security]
tlsEnabled=true
checkPeerFingerprints=true
certificate={certificate}
keySize=2048

[log]
level=INFO
toFile=false
guiDebug=false

[gui]
enableUpdateCheck=false
startCoreWithGui=false
closeToTray=false

[server]
externalConfig=true
externalConfigFile={layout.as_posix()}
enableClipboard={str(config.clipboard).lower()}
clipboardSize=4
protocol=barrier
enableHeartbeat=true
heartbeat=1000
enableSwitchDelay=false
enableSwitchDoubleTap=false
disableLockToComputer=true
defaultLockToComputerState=false
gridWidth=2
gridHeight=1

[computer_{server}]
name={server}
modifierCtrl=ctrl
modifierAlt=alt
modifierSuper=super

[computer_{client}]
name={client}
modifierCtrl=ctrl
modifierAlt=alt
modifierSuper=super
"""
    path = root / "Deskflow.conf"
    atomic_write(path, text)
    return path
