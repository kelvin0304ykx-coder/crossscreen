import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QEvent, QLockFile, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from . import __version__
from .config import AppConfig, app_data_dir, atomic_write, default_host
from .engine import Engine
from .security import ensure_identity, make_pairing, parse_pairing
from .permissions import mac_accessibility_granted, request_mac_accessibility


def app_icon():
    pix = QPixmap(96, 96)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2468ed"))
    p.drawRoundedRect(3, 3, 90, 90, 23, 23)
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI", 44))
    p.drawText(pix.rect(), Qt.AlignCenter, "⇄")
    p.end()
    return QIcon(pix)


class PairDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("导入另一台电脑")
        self.setMinimumWidth(550)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("在另一台电脑点击「复制本机配对信息」，然后粘贴到这里。"))
        self.field = QLineEdit()
        self.field.setPlaceholderText("粘贴完整配对信息")
        self.field.setMaxLength(2048)
        layout.addWidget(self.field)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("配对")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)


class Window(QMainWindow):
    def __init__(self, root: Path, qa_report=None):
        super().__init__()
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine_root = root / "engine"
        self.fingerprint = ensure_identity(self.engine_root)
        self.path = root / "app.json"
        self.config_error = ""
        if self.path.exists():
            try:
                self.config = AppConfig.load(self.path)
            except (OSError, ValueError, TypeError) as exc:
                self.config = self.defaults()
                self.config_error = "已保存的配置无法读取，请检查后重新配对。"
        else:
            self.config = self.defaults()
        self.engine = Engine(self)
        self.engine.status.connect(self.set_status)
        self.engine.running_changed.connect(self.running_changed)
        self.qa_report = Path(qa_report) if qa_report else None
        self.qa = {"typed_probe": False, "connected": False, "mouse_click": False}
        self.setWindowTitle("跨屏")
        self.setWindowIcon(app_icon())
        self.resize(760, 740)
        self.setMinimumSize(680, 650)
        self.build_ui()
        self.refresh_layout()
        if self.config_error:
            self.set_status("error", self.config_error)

    @staticmethod
    def defaults():
        is_mac = sys.platform == "darwin"
        return AppConfig(role="client" if is_mac else "server", local_name="mac" if is_mac else "windows",
                         peer_name="windows" if is_mac else "mac", host=default_host())

    def build_ui(self):
        container = QWidget()
        self.setCentralWidget(container)
        box = QVBoxLayout(container)
        box.setContentsMargins(32, 24, 32, 22)
        box.setSpacing(14)
        title = QLabel("跨屏")
        title.setObjectName("title")
        box.addWidget(title)
        sub = QLabel("一套键鼠，自然跨越 Windows 与 Mac")
        sub.setObjectName("muted")
        box.addWidget(sub)

        self.layout_preview = QLabel()
        self.layout_preview.setObjectName("screens")
        self.layout_preview.setAlignment(Qt.AlignCenter)
        self.layout_preview.setMinimumHeight(82)
        box.addWidget(self.layout_preview)

        status_box = QFrame()
        status_box.setObjectName("statusBox")
        status_layout = QVBoxLayout(status_box)
        self.status_title = QLabel("●  尚未开始共享")
        self.status_title.setObjectName("statusTitle")
        status_layout.addWidget(self.status_title)
        self.status_detail = QLabel("先交换两端配对信息，再分别点击开始。")
        self.status_detail.setWordWrap(True)
        status_layout.addWidget(self.status_detail)
        box.addWidget(status_box)

        self.settings_widget = QWidget()
        self.settings_widget.setMinimumHeight(150)
        form = QFormLayout(self.settings_widget)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(10)
        self.role = QComboBox()
        self.role.addItems(["共享键鼠（接收器插在这台）", "接收键鼠（控制这台电脑）"])
        self.role.setCurrentIndex(0 if self.config.role == "server" else 1)
        self.role.currentIndexChanged.connect(self.refresh_layout)
        form.addRow("本机角色", self.role)
        self.host = QLineEdit(self.config.host)
        self.host.setPlaceholderText("Windows 的局域网 IP 地址")
        self.host.setMaxLength(253)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(self.config.port)
        address = QHBoxLayout()
        address.addWidget(self.host, 1)
        address.addWidget(self.port)
        form.addRow("Windows 地址", address)
        self.side = QComboBox()
        self.side.addItems(["Mac 放在 Windows 右边", "Mac 放在 Windows 左边"])
        self.side.setCurrentIndex(0 if self.config.side == "right" else 1)
        self.side.currentIndexChanged.connect(self.refresh_layout)
        form.addRow("屏幕位置", self.side)
        box.addWidget(self.settings_widget)

        row = QHBoxLayout()
        self.copy = QPushButton("复制本机配对信息")
        self.copy.clicked.connect(self.copy_pairing)
        row.addWidget(self.copy)
        self.pair = QPushButton("导入另一台电脑")
        self.pair.clicked.connect(self.import_pairing)
        row.addWidget(self.pair)
        box.addLayout(row)
        self.pair_status = QLabel()
        self.pair_status.setObjectName("muted")
        self.refresh_pair_status()
        box.addWidget(self.pair_status)

        actions = QHBoxLayout()
        self.start = QPushButton("开始共享")
        self.start.setObjectName("primary")
        self.start.setMinimumHeight(44)
        self.start.clicked.connect(self.start_engine)
        self.stop = QPushButton("停止共享")
        self.stop.setMinimumHeight(44)
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.engine.stop)
        actions.addWidget(self.start, 2)
        actions.addWidget(self.stop, 1)
        box.addLayout(actions)

        self.probe = QLineEdit()
        self.probe.setPlaceholderText("试一下：把鼠标移到这台，点这里输入文字")
        self.probe.setMaxLength(256)
        self.probe.textChanged.connect(self.probe_text)
        self.probe.installEventFilter(self)
        box.addWidget(self.probe)
        tip = QLabel("鼠标在哪台，键盘就控制哪台。紧急切回 Windows：Ctrl + Alt + Shift + F12")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        box.addWidget(tip)
        footer = QHBoxLayout()
        if sys.platform == "darwin":
            permissions = QPushButton("允许 Mac 键鼠控制")
            permissions.setFlat(True)
            permissions.clicked.connect(self.open_permissions)
            footer.addWidget(permissions)
        footer.addStretch()
        about = QPushButton(f"关于 · {__version__}")
        about.setFlat(True)
        about.clicked.connect(self.about)
        footer.addWidget(about)
        box.addLayout(footer)
        box.addStretch()
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fc; color: #182238; font-size: 14px; }
            QLabel#title { font-size: 30px; font-weight: 700; }
            QLabel#muted { color: #69758b; font-size: 12px; }
            QLabel#screens { background: #eaf0ff; border: 1px solid #d5e2ff;
                              border-radius: 14px; color: #2458bd; font-size: 21px; font-weight: 600; }
            QFrame#statusBox { background: white; border: 1px solid #e0e5ef; border-radius: 12px; padding: 8px; }
            QFrame#statusBox QLabel { background: transparent; border: none; padding: 0; }
            QLabel#statusTitle { font-weight: 600; font-size: 17px; }
            QLineEdit, QSpinBox, QComboBox { background: white; border: 1px solid #d3dbea;
                border-radius: 7px; padding: 8px; min-height: 18px; }
            QComboBox::drop-down { border: none; width: 24px; }
            QPushButton { background: white; border: 1px solid #d3dbea; border-radius: 8px; padding: 9px 14px; }
            QPushButton:hover { background: #edf3ff; border-color: #9bbafa; }
            QPushButton#primary { background: #2468ed; color: white; border: none; font-weight: 600; }
            QPushButton#primary:hover { background: #1c56cb; }
            QPushButton:disabled { color: #a2aaba; background: #edf0f5; border-color: #e1e5ec; }
            QPushButton:flat { border: none; background: transparent; color: #69758b; font-size: 12px; }
        """)

    def collect(self):
        return replace(self.config, role="server" if self.role.currentIndex() == 0 else "client",
                       host=self.host.text().strip(), port=self.port.value(),
                       side="right" if self.side.currentIndex() == 0 else "left").validate()

    def refresh_layout(self, *_):
        if not hasattr(self, "side"):
            return
        self.layout_preview.setText("▣  Windows     ⇄     ▣  Mac" if self.side.currentIndex() == 0
                                    else "▣  Mac     ⇄     ▣  Windows")

    def refresh_pair_status(self):
        if self.config.peer_fingerprint:
            self.pair_status.setText(f"已配对 · {self.config.peer_name}  /  {self.config.peer_fingerprint[:16]}")
        else:
            self.pair_status.setText("尚未配对 · 配对信息只包含连接地址和设备公钥指纹")

    def copy_pairing(self):
        try:
            self.config = self.collect()
            data = make_pairing(self.config.local_name, self.config.host, self.config.port,
                                self.fingerprint, self.config.role)
            QApplication.clipboard().setText(data)
            self.config.save(self.path)
            self.set_status("ready", "本机配对信息已复制，请在另一台电脑导入。")
        except ValueError as exc:
            QMessageBox.warning(self, "请检查配置", str(exc))

    def import_pairing(self):
        dialog = PairDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            peer = parse_pairing(dialog.field.text().strip())
            current = self.collect()
            if peer["role"] == current.role:
                raise ValueError("两台角色相同；接收器所在电脑选择共享，另一台选择接收。")
            if peer["name"] == current.local_name:
                raise ValueError("这是本机配对信息，请复制另一台电脑的信息。")
            self.config = replace(current, peer_name=peer["name"], peer_fingerprint=peer["fingerprint"],
                                  host=peer["host"] if current.role == "client" else current.host,
                                  port=peer["port"] if current.role == "client" else current.port).validate()
            self.host.setText(self.config.host)
            self.port.setValue(self.config.port)
            self.config.save(self.path)
            self.refresh_pair_status()
            self.set_status("ready", "配对信息已保存，两台都点击开始即可连接。")
        except ValueError as exc:
            QMessageBox.warning(self, "配对未完成", str(exc))

    def start_engine(self):
        try:
            self.config = self.collect()
            self.config.save(self.path)
            if not mac_accessibility_granted():
                self.set_status("permission", "请点击下方「允许 Mac 键鼠控制」，在辅助功能中允许跨屏，再点击开始。")
                return
            self.engine.start(self.config, self.engine_root)
        except (ValueError, OSError) as exc:
            self.set_status("error", str(exc))

    def open_permissions(self):
        if request_mac_accessibility():
            self.set_status("ready", "辅助功能权限已就绪，可以开始共享。")
        else:
            QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"))
            self.set_status("permission", "请在辅助功能中允许跨屏；如果尚未列出，可添加 Applications 文件夹里的 CrossScreen.app。")

    def running_changed(self, running):
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.settings_widget.setEnabled(not running)
        self.copy.setEnabled(not running)
        self.pair.setEnabled(not running)

    def set_status(self, state, detail):
        titles = {"starting": "正在连接", "connected": "已连接", "waiting": "等待连接",
                  "error": "需要检查", "stopped": "已停止", "ready": "准备就绪",
                  "permission": "需要系统授权", "mac": "正在控制 Mac", "windows": "正在控制 Windows"}
        color = "#19825a" if state in ("connected", "mac", "windows") else "#b34b31" if state in ("error", "permission") else "#456381"
        self.status_title.setText("●  " + titles.get(state, state))
        self.status_title.setStyleSheet(f"color: {color};")
        self.status_detail.setText(detail)
        self.qa["state"] = state
        self.qa["detail"] = detail
        if state == "connected":
            self.qa["connected"] = True
        self.save_qa()

    def eventFilter(self, watched, event):
        if watched is self.probe and event.type() == QEvent.MouseButtonPress:
            self.qa["mouse_click"] = True
            self.save_qa()
        return super().eventFilter(watched, event)

    def probe_text(self, text):
        # Verification writes booleans only, never user-entered text.
        if self.qa_report and text == "CrossScreen42":
            self.qa["typed_probe"] = True
            self.save_qa()

    def save_qa(self):
        if self.qa_report:
            atomic_write(self.qa_report, json.dumps(self.qa))

    def about(self):
        QMessageBox.information(self, "关于跨屏", f"跨屏 {__version__}\n\n"
            "键鼠共享组件：Deskflow 1.27.0（未经修改）\n"
            "使用 TLS 加密与设备证书配对。输入内容不写入日志；剪贴板共享关闭。\n\n"
            "https://github.com/deskflow/deskflow\n第三方许可随程序提供。")

    def closeEvent(self, event):
        if self.engine.stop():
            event.accept()
        else:
            event.ignore()
            QMessageBox.warning(self, "停止尚未完成", "共享组件仍在退出。请稍后再次点击停止，再关闭窗口。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--qa-report")
    parser.add_argument("--screenshot")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI", 10))
    app.setApplicationName("CrossScreen")
    app.setApplicationDisplayName("跨屏")
    app.setOrganizationName("CrossScreen")
    root = app_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / "app.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "跨屏已在运行", "本机已经打开跨屏，请切换到已有窗口。")
        return 0
    window = Window(root, args.qa_report)
    app.aboutToQuit.connect(window.engine.stop)
    window.show()
    if args.start:
        QTimer.singleShot(800, window.start_engine)
    if args.screenshot:
        def capture():
            window.grab().save(args.screenshot)
            window.close()
        QTimer.singleShot(1200, capture)
    return app.exec()
