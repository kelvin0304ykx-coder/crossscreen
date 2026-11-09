"""Read and publish native file references without reading other clipboard data."""

from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import struct
import sys


@dataclass(frozen=True)
class ClipboardSnapshot:
    revision: int
    paths: tuple[Path, ...]
    empty: bool
    engine_owned: bool = False


class NativeClipboard:
    """Use from the GUI thread. Windows writes require its native window handle."""

    def __init__(self, hwnd: int = 0):
        if sys.platform == "win32":
            self._backend = _WindowsClipboard(hwnd)
        elif sys.platform == "darwin":
            self._backend = _MacClipboard()
        else:
            raise RuntimeError("Native file clipboard is unavailable on this platform")

    def revision(self) -> int:
        return self._backend.revision()

    def read(self) -> ClipboardSnapshot:
        return self._backend.read()

    def put(self, paths: Iterable[Path], expected_revision: int) -> int | None:
        paths = tuple(Path(path) for path in paths)
        if not paths or any(not path.is_absolute() or "\0" in str(path) for path in paths):
            raise ValueError("The file clipboard requires absolute paths")
        return self._backend.replace(paths, expected_revision)

    def clear(self, expected_revision: int) -> int | None:
        return self._backend.replace((), expected_revision)


@contextmanager
def _clipboard_errors(native_error):
    try:
        yield
    except native_error:
        raise OSError("Native clipboard operation failed") from None


class _WindowsClipboard:
    def __init__(self, hwnd):
        import win32clipboard
        from pywintypes import error
        from win32con import CF_HDROP

        self._board = win32clipboard
        self._error = error
        self._hwnd = hwnd
        self._hdrop = CF_HDROP
        with _clipboard_errors(error):
            self._ownership = win32clipboard.RegisterClipboardFormat("Deskflow Ownership")
            self._drop_effect = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")

    @contextmanager
    def _open(self):
        with _clipboard_errors(self._error):
            self._board.OpenClipboard(self._hwnd)
            try:
                yield self._board
            finally:
                self._board.CloseClipboard()

    def revision(self):
        return self._board.GetClipboardSequenceNumber() & 0xFFFFFFFF

    def read(self):
        with self._open() as board:
            owned = bool(board.IsClipboardFormatAvailable(self._ownership))
            kind = board.EnumClipboardFormats(0)
            while kind in (self._ownership, self._drop_effect):
                kind = board.EnumClipboardFormats(kind)
            paths = ()
            if board.IsClipboardFormatAvailable(self._hdrop):
                paths = tuple(Path(name) for name in board.GetClipboardData(self._hdrop))
                if any(not path.is_absolute() for path in paths):
                    paths = ()
            # Rendering a promised CF_HDROP can advance the sequence number.
            return ClipboardSnapshot(self.revision(), paths, kind == 0, owned)

    def replace(self, paths, expected_revision):
        payload = b""
        if paths:
            header = struct.Struct("<IiiII")  # DROPFILES with fWide = TRUE.
            names = "\0".join(map(str, paths)) + "\0\0"
            payload = header.pack(header.size, 0, 0, 0, 1) + names.encode("utf-16-le", "surrogatepass")

        with self._open() as board:
            if self.revision() != expected_revision:
                return None
            if not self._hwnd:
                raise RuntimeError("Windows clipboard writes require a window handle")
            board.EmptyClipboard()
            # Publish metadata before CF_HDROP so a failed write exposes no file list.
            board.SetClipboardData(self._ownership, b"\0")
            if paths:
                board.SetClipboardData(self._drop_effect, struct.pack("<I", 1))  # DROPEFFECT_COPY
                board.SetClipboardData(self._hdrop, payload)
            return self.revision()


class _MacClipboard:
    def __init__(self):
        from AppKit import NSPasteboard, NSPasteboardContentsCurrentHostOnly, NSPasteboardTypeFileURL
        from Foundation import NSURL
        from objc import error

        self._error = error
        self._file_url = NSPasteboardTypeFileURL
        self._local_only = NSPasteboardContentsCurrentHostOnly
        self._url = NSURL
        with _clipboard_errors(error):
            self._board = NSPasteboard.generalPasteboard()
        if self._board is None:
            raise OSError("macOS pasteboard is unavailable")

    def revision(self):
        with _clipboard_errors(self._error):
            return self._board.changeCount()

    def _read_paths(self):
        items = self._board.pasteboardItems()
        if items is None:
            raise OSError("macOS pasteboard items are unavailable")
        paths = []
        for item in items:
            if self._file_url not in (item.types() or ()):
                continue
            raw = item.stringForType_(self._file_url)
            if raw is None:
                raise OSError("macOS pasteboard file URL is unavailable")
            url = self._url.URLWithString_(raw)
            if not url or not url.isFileURL() or url.host() not in (None, "", "localhost"):
                return ()
            # Finder sometimes stores a reference such as file:///.file/id=….
            resolved = url.filePathURL()
            path = resolved.path() if resolved else None
            if not path or not Path(path).is_absolute() or "\0" in path:
                return ()
            paths.append(Path(path))
        return tuple(paths)

    def read(self):
        with _clipboard_errors(self._error):
            revision = self.revision()
            types = self._board.types() or ()
            paths = self._read_paths() if self._file_url in types else ()
            if self.revision() != revision:
                raise OSError("macOS pasteboard changed during read")
            return ClipboardSnapshot(revision, paths, not types)

    def replace(self, paths, expected_revision):
        with _clipboard_errors(self._error):
            urls = [self._url.fileURLWithPath_(str(path)) for path in paths]
            if any(url is None for url in urls):
                raise OSError("Could not create macOS pasteboard file URLs")
            if self.revision() != expected_revision:
                return None
            if not urls:
                return self._board.clearContents()

            revision = self._board.prepareForNewContentsWithOptions_(self._local_only)
            if revision != expected_revision + 1 or self.revision() != revision:
                return None
            written = self._board.writeObjects_(urls)
            if self.revision() != revision:
                return None
            if not written:
                raise OSError("Could not publish macOS pasteboard file URLs")
            return revision
