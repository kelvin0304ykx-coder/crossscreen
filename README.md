# 跨屏 CrossScreen

一套键盘和鼠标，在 Windows 与 Mac 之间自然跨屏。鼠标移到哪台电脑，键盘输入就跟到哪台。

[下载测试版与使用指南](https://www.sibaihuo.com/crossscreen/) · [GitHub Releases](https://github.com/kelvin0304ykx-coder/crossscreen/releases)

## 下载

| 设备 | 测试版下载 |
| --- | --- |
| Windows 11，Intel / AMD 64 位 | [Windows x64](https://www.sibaihuo.com/crossscreen/downloads/0.2.2-beta.1/CrossScreen-0.2.2-beta.1-Windows-x64.zip) |
| Apple 芯片 Mac，M 系列 | [macOS arm64](https://www.sibaihuo.com/crossscreen/downloads/0.2.2-beta.1/CrossScreen-0.2.2-beta.1-macOS-arm64.zip) |

下载并解压后打开 CrossScreen.exe 或 CrossScreen.app。程序包包含运行依赖，首次启动会在本机生成独立设备身份。

上面的公开下载包为 **0.2.2-beta.1**，桌面程序显示 **0.2.2**。更新两端程序时可保留原有配对。

本仓库的 `main` 分支已更新到 **0.3.0** 源码，新增图片和视频文件跨机粘贴，并修复 Windows 通过不同入口使用同一配置目录时的写入问题。体验文件粘贴需要两端都运行 0.3.0 或更新版本，可按下文从源码构建。

## 第一次使用

1. 把无线键鼠的 USB 接收器插在 Windows 上。两台电脑连接同一个局域网，Wi-Fi 和网线可以混用。
2. Windows 完整解压程序文件夹，再打开其中的 CrossScreen.exe。Mac 将 CrossScreen.app 放入自己的 Applications 文件夹后打开。
3. Windows 选择「共享键鼠」，Mac 选择「接收键鼠」。地址填写 Windows 窗口中显示的局域网 IPv4 地址，两端端口一致。
4. Windows 点击「复制本机配对信息」，通过你已有的聊天工具或文本文件把整段信息转到 Mac，在 Mac 点「导入另一台电脑」并粘贴。再把 Mac 的配对信息反向导入 Windows。配对信息包含地址、设备名和公钥指纹。
5. 按你面向屏幕时的实际摆放位置选择 Mac 在左边还是右边。布局由共享键鼠的 Windows 设置决定。
6. Mac 点击「允许 Mac 键鼠控制」，在系统设置的隐私与安全性中允许 CrossScreen 的辅助功能访问。若 Windows 防火墙询问网络访问，请允许用于两台电脑互通的私人网络。
7. 两端点击「开始共享」。出现「已连接」后，把鼠标推过相邻屏幕边缘，在另一台电脑点一个输入框试打字，再移回来。

配对信息会保存在各自电脑。每次开机后打开两端应用并点击开始，窗口可以最小化。关闭窗口或停止共享会结束本程序的共享连接。

## 复制文字与拖文件

在一台电脑复制文字，把鼠标移到另一台，再粘贴。Windows 使用 Ctrl+C / Ctrl+V，控制 Mac 时用键盘上的 Windows 键+C / V，对应 Command+C / V。

先在接收端打开目标文件夹。从 Finder 或资源管理器按住普通文件，拖过两块屏幕的相邻边缘，到目标窗口的文件列表空白处松手。副本出现在该文件夹里，同名文件会追加序号。每次可拖 1–64 个普通文件，单个上限为 16 GiB。文件夹请先压缩成 ZIP。此上限是程序限制，大文件速度取决于磁盘和网络。

首次往 Mac 拖文件时，按系统提示允许跨屏访问 Finder。文件通道默认使用键鼠端口的下一端口（键鼠端口为 65535 时使用 65534），两端需能互通。

0.2.2-beta.1 在一组 Windows 11 x64 和 Apple 芯片 Mac 上实测，六次连续从 Finder 快拖到资源管理器均产生了内容一致的副本。个别情况下，传完后 Mac 仍短暂报告左键按住，后续拖动已成功。

## 复制粘贴图片和视频文件（0.3.0）

1. 两端运行 0.3.0 或更新版本，Windows 勾选「共享剪贴板」后开始共享。
2. 在资源管理器或 Finder 选中图片或视频文件，按 Ctrl+C（Windows）或 Command+C（Mac）。
3. 把鼠标移到另一台电脑，看到「剪贴板文件已就绪」后，在文件夹或支持粘贴文件附件的聊天输入框按 Ctrl+V（Windows）或 Command+V（Mac）。

常见的 PNG、JPG、MP4、MOV 文件可双向传输。每次可复制 1–64 个文件，合计最多 16 GiB。文件通过已配对的局域网通道传输，收到后暂存在对端的应用数据目录。暂存上限为 32 GiB；超过 24 小时且未被使用的缓存会在后续接收时清理。聊天应用是否接受文件粘贴取决于该应用；也可以先粘贴到文件夹，再选为附件。

## 键位与恢复

- 在 Mac 上，键盘的 Windows 键对应 Command，Ctrl 保持 Ctrl。
- Ctrl + Alt + Shift + F12 切回 Windows。
- Ctrl + Alt + Shift + F11 切到 Mac。
- Windows 完整文件夹需要保留，单独移动 exe 会缺少运行组件。
- Mac 当前为本地签名测试版，尚未经过 Apple 公证。若系统拦截，请先确认下载来源和 SHA-256，再按 macOS 系统设置提供的「仍要打开」入口决定是否运行。
- 更新 Mac 程序后，若辅助功能显示已开启但程序仍提示未授权，退出跨屏，在辅助功能列表移除旧的 CrossScreen，再添加并允许当前的 CrossScreen.app，然后重新打开。
- 本程序与 Deskflow 使用同一个底层组件，使用前请退出另一份同类组件。

## 当前范围

首轮实测包含 Windows 11 x64 与 Apple 芯片 Mac 的双向鼠标跨屏、键盘跟随、停止和重新启动。Windows 安全桌面、Mac 锁屏与安全输入、休眠唤醒、游戏输入和不同系统版本尚需更多测试反馈。当前下载包分别面向 Windows x64 和 Apple 芯片 Mac，Intel Mac、Windows ARM 与 Linux 尚未提供安装包。

## 连接与数据

通信使用 TLS，并校验配对设备的 SHA-256 证书指纹。输入内容不写入日志。剪贴板共享默认开启，可在 Windows 开始共享前关闭。文字剪贴板单次上限 4 MiB，0.3.0 的图片和视频文件使用上文所述的独立文件通道。应用不注册系统服务或开机启动项。

本机配置目录为 Windows 的 `%LOCALAPPDATA%\CrossScreen`，以及 Mac 的 `~/Library/Application Support/CrossScreen`。身份私钥仅存放在各自配置目录，分发包和配对信息均不包含该私钥。

## 源码与构建

本项目提供桌面界面、设备配置、证书配对与共享组件的生命周期管理。键鼠捕获与跨系统传输使用未经修改的 Deskflow 1.27.0。

需要 Python 3.10 或以上版本。先建立虚拟环境，安装 `requirements.txt`，再运行以下命令。

```text
python tools/fetch_engine.py
python main.py
python -m pytest -q tests
python tools/build.py
```

Windows 包需在 Windows x64 构建。Mac 包需在 Apple 芯片 Mac 构建，并使用系统的 codesign 工具。本地源码构建与上架分发涉及不同的签名身份，首次授权应对应自己实际运行的程序。

`tools/fetch_engine.py` 从官方发布下载固定版本，先校验 SHA-256，再解包。`tools/build.py` 生成当前系统的桌面包。

## 许可证

界面和配置层按 GPL-3.0-only 提供。Deskflow 保留原 GPL-2.0-only WITH LicenseRef-OpenSSL-Exception 许可。Qt / PySide6、Python 和其他组件保留各自许可，文本及对应源码入口见 `licenses/` 和 `THIRD_PARTY_NOTICES.md`。

## 反馈

请在 GitHub Issues 中填写两端系统版本、芯片型号、程序版本、屏幕位置及复现步骤。配对私钥、完整个人文件和工作内容不应附在问题报告中。
