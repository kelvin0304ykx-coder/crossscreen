"""Media file clipboard over the paired file channel."""
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
import time

from .filelink import LIMIT, is_media

CACHE_AGE = 24 * 60 * 60
CACHE_LIMIT = 2 * LIMIT


@dataclass
class PendingCopy:
    token: str
    folder: Path
    paths: tuple[Path, ...] = ()


@dataclass
class FileSelection:
    token: str
    paths: tuple[Path, ...]
    remote: bool
    published: bool
    acknowledged: bool = False


class FileClipboard:
    def __init__(self, link, config, cache, board, notice):
        self.link = link
        self.config = config
        self.cache = cache.parent.resolve() / cache.name
        self.board = board
        self.notice = notice
        self.reset()

    def reset(self):
        self.enabled = False
        self.revision = None
        self.grace_until = 0
        self.outgoing = None
        self.pending = None
        self.selection = None
        self.clear_revision = None

    def switched(self, entering):
        # macOS Deskflow clears its pasteboard without an ownership marker.
        receiving = self.pending is not None or (self.selection is not None and self.selection.remote)
        self.grace_until = time.monotonic() + .4 if entering and receiving else 0
        self.poll()

    def reject_pending(self):
        if self.pending is not None:
            self.link.send({"op": "clip-decline", "id": self.pending.token})
            self.pending = None

    def sample(self):
        if self.board.revision() == self.revision:
            return
        snapshot = self.board.read()
        first = self.revision is None
        self.revision = snapshot.revision
        if first:
            return
        if snapshot.empty and (snapshot.engine_owned or time.monotonic() < self.grace_until):
            self.grace_until = 0
            if self.selection is not None:
                self.selection.published = False
            return

        self.reject_pending()
        if self.outgoing:
            self.link.revoke_clipboard(self.outgoing)
            self.outgoing = None
        self.selection = None
        if snapshot.engine_owned or not snapshot.paths or not all(is_media(p.name) for p in snapshot.paths):
            return
        try:
            self.outgoing = self.link.offer(snapshot.paths, clipboard=True)
        except ValueError as exc:
            self.notice(str(exc))
        except OSError:
            self.notice("复制的文件已变化，请重新复制")
        if self.outgoing:
            self.selection = FileSelection(self.outgoing, snapshot.paths, remote=False, published=True)
            self.notice("正在传送剪贴板文件，完成后可在对端粘贴")

    def poll(self):
        if not self.enabled or not self.link.connected.is_set():
            return
        try:
            if self.clear_revision is not None:
                revision = self.board.clear(self.clear_revision)
                self.clear_revision = None
                if revision is not None:
                    self.revision = revision
            self.sample()
            if self.pending is not None and self.pending.paths:
                copy, self.pending = self.pending, None
                self.selection = FileSelection(copy.token, copy.paths, remote=True, published=False)
            selection = self.selection
            if selection is None:
                return
            if not selection.published:
                revision = self.board.put(selection.paths, self.revision)
                if revision is None:
                    return
                self.revision = revision
                selection.published = True
            if selection.remote and not selection.acknowledged:
                selection.acknowledged = self.link.send({"op": "clip-saved", "id": selection.token})
                if selection.acknowledged:
                    self.notice("剪贴板文件已就绪 · 可粘贴到文件夹或附件输入框")
        except (OSError, RuntimeError):
            # Another app may have the system clipboard open. Retry on the next tick.
            return

    def stage(self, size):
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.cache.resolve() != self.cache:
            raise OSError("invalid clipboard cache")
        protected = {path.parent for path in self.board.read().paths}
        protected.update(self.link.clipboard_folders())
        if self.selection is not None:
            protected.update(path.parent for path in self.selection.paths)
        used = 0
        now = time.time()
        for folder in self.cache.iterdir():
            if not re.fullmatch(r"copy-[a-z0-9_]{8}", folder.name) or folder.is_symlink() or not folder.is_dir():
                continue
            if folder.resolve().parent != self.cache:
                continue
            files = tuple(folder.iterdir())
            if not all(path.is_file() and not path.is_symlink() for path in files):
                continue
            if folder not in protected and now - folder.stat().st_mtime > CACHE_AGE:
                for path in files:
                    path.unlink()
                folder.rmdir()
            else:
                used += sum(path.stat().st_size for path in files)
        if used + size > CACHE_LIMIT or shutil.disk_usage(self.cache).free < size + 64 * 1024**2:
            raise ValueError("剪贴板暂存空间不足")
        return Path(tempfile.mkdtemp(prefix="copy-", dir=self.cache))

    def receive(self, msg):
        token, size, count = msg.get("id"), msg.get("size"), msg.get("count")
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            return
        if (type(size) is not int or not 0 <= size <= LIMIT
                or type(count) is not int or not 1 <= count <= 64):
            self.link.send({"op": "clip-decline", "id": token})
            return
        if self.pending is not None and self.pending.token == token:
            return
        self.poll()
        self.reject_pending()
        try:
            folder = self.stage(size)
        except (OSError, RuntimeError, ValueError):
            self.link.send({"op": "clip-decline", "id": token})
            self.notice("无法暂存剪贴板文件，请检查可用空间后重新复制")
            return
        if not self.link.accept(token, folder, count, clipboard_size=size):
            folder.rmdir()
            return
        self.pending = PendingCopy(token, folder)
        self.notice("正在接收剪贴板文件，传完即可粘贴")

    def message(self, msg):
        op = msg["op"]
        if op == "online":
            self.reset()
            self.revision = self.board.revision()
            self.link.send({"op": "layout", "clipboard_files": 1,
                            "clipboard_enabled": self.config.clipboard})
        elif op == "offline":
            self.reset()
        elif op == "layout" and msg.get("clipboard_files") == 1:
            self.enabled = (self.config.clipboard if self.config.role == "server"
                            else msg.get("clipboard_enabled") is True)
            if self.enabled:
                self.notice("文件通道已连接 · 支持拖放及图片、视频文件粘贴")
        elif self.enabled:
            token = msg.get("id")
            if op == "clip-offer":
                self.receive(msg)
            elif op == "clip-ready" and self.pending is not None and self.pending.token == token:
                self.pending.paths = msg["paths"]
                self.poll()
            elif op == "clip-clear":
                if self.pending is not None and self.pending.token == token:
                    self.pending = None
                if self.selection is not None and self.selection.remote and self.selection.token == token:
                    if self.selection.published:
                        self.clear_revision = self.revision
                    self.selection = None
                    self.poll()
            elif op == "clip-saved" and token == self.outgoing:
                self.notice("对端剪贴板文件已就绪 · 可粘贴")
            elif op in ("clip-decline", "clip-failed") and token == self.outgoing:
                self.outgoing = None
                self.notice("剪贴板文件未送达，请重新复制")
