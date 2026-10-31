"""Build a self-contained local desktop application on the current platform."""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    is_mac = sys.platform == "darwin"
    engine = ROOT / ("vendor/macos" if is_mac else "vendor/windows/deskflow-1.27.0-win-x64-portable")
    if not engine.exists():
        raise SystemExit("Engine directory is missing")
    from PySide6.QtWidgets import QApplication
    from crossscreen.app import app_icon
    app = QApplication([])
    icon = ROOT / "build-icon.png"
    app_icon().pixmap(256, 256).save(str(icon))
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "CrossScreen", "--icon", str(icon),
            "--add-data", f"{ROOT / 'NOTICE.txt'}:.", "--add-data", f"{ROOT / 'LICENSE'}:.",
            "--add-data", f"{ROOT / 'licenses'}:licenses",
            "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.md'}:.",
            "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
            "--exclude-module", "PySide6.QtPdf", "--exclude-module", "tkinter"]
    if is_mac:
        args += ["--osx-bundle-identifier", "cn.akai.crossscreen", "--target-architecture", "arm64"]
    else:
        args += ["--version-file", str(ROOT / "tools/version_info.txt")]
    args.append(str(ROOT / "main.py"))
    build_env = os.environ.copy()
    if not is_mac:
        # Third-party tools on PATH can ship an ICU DLL with version-suffixed
        # exports. Qt uses Windows' ICU; bundling that other DLL breaks imports.
        system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        build_env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(system / "System32"), str(system)])
    subprocess.run(args, cwd=ROOT, env=build_env, check=True)
    if is_mac:
        bundle = ROOT / "dist/CrossScreen.app"
        # Keep the unmodified native engine out of PyInstaller's dependency
        # analysis: it has its own Qt build and framework symlinks.
        shutil.copytree(engine, bundle / "Contents/Resources/engine", symlinks=True, dirs_exist_ok=True)
        info_path = bundle / "Contents/Info.plist"
        with info_path.open("rb") as f:
            info = plistlib.load(f)
        info.update(CFBundleDisplayName="跨屏", CFBundleShortVersionString="0.1.0", CFBundleVersion="1",
                    NSAccessibilityUsageDescription="用于在本机接收和使用另一台电脑共享的键盘与鼠标。",
                    NSLocalNetworkUsageDescription="通过局域网连接你的另一台电脑，共享键盘与鼠标。")
        with info_path.open("wb") as f:
            plistlib.dump(info, f)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
    else:
        bundle = ROOT / "dist/CrossScreen"
        shutil.copytree(engine, bundle / "_internal/engine", dirs_exist_ok=True)
    shutil.copy2(ROOT / "README.md", bundle.parent / "使用说明.txt" if is_mac else bundle / "使用说明.txt")
    print("BUILT", bundle)


if __name__ == "__main__":
    main()
