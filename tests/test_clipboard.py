from pathlib import Path
from threading import Event
from types import SimpleNamespace


class Board:
    def __init__(self):
        self.copy()

    def revision(self):
        return self.value.revision

    def read(self):
        return self.value

    def copy(self, paths=(), *, empty=False, engine_owned=False):
        revision = self.value.revision + 1 if hasattr(self, "value") else 0
        self.value = SimpleNamespace(revision=revision, paths=tuple(paths), empty=empty,
                                     engine_owned=engine_owned)
        return revision

    def put(self, paths, expected_revision):
        if expected_revision == self.revision():
            return self.copy(paths)

    def clear(self, expected_revision):
        if expected_revision == self.revision():
            return self.copy(empty=True)


class Link:
    def __init__(self):
        self.connected = Event()
        self.connected.set()
        self.messages = []
        self.offers = []
        self.accepted = []
        self.revoked = []

    def send(self, message):
        self.messages.append(message)
        return True

    def offer(self, paths, *, clipboard):
        assert clipboard
        self.offers.append(tuple(paths))
        return "a" * 32

    def accept(self, token, folder, count, *, clipboard_size):
        self.accepted.append((token, folder, count, clipboard_size))
        return True

    def revoke_clipboard(self, token):
        self.revoked.append(token)

    def clipboard_folders(self):
        return {folder for _, folder, _, _ in self.accepted}


def connect(tmp_path, *, role="server", enabled=True, peer=True):
    from crossscreen.clipboard import FileClipboard

    board, link = Board(), Link()
    clipboard = FileClipboard(link, SimpleNamespace(role=role, clipboard=enabled),
                              tmp_path / "cache", board, lambda _: None)
    clipboard.message({"op": "online"})
    if peer:
        clipboard.message({"op": "layout", "clipboard_files": 1, "clipboard_enabled": enabled})
    return board, link, clipboard


def receive(clipboard, link, data=b"media"):
    token = "b" * 32
    clipboard.message({"op": "clip-offer", "id": token, "count": 1, "size": len(data)})
    path = link.accepted[-1][1] / "图片.png"
    path.write_bytes(data)
    return {"op": "clip-ready", "id": token, "paths": (path,)}


def test_replacement_during_transfer_does_not_overwrite_local_clipboard(tmp_path):
    board, link, clipboard = connect(tmp_path)
    ready = receive(clipboard, link)
    revision = board.copy()  # A new non-file clipboard from the local user.
    clipboard.message(ready)
    assert board.revision() == revision
    assert board.read().paths == ()
    assert {"op": "clip-decline", "id": ready["id"]} in link.messages
    assert not any(msg["op"] == "clip-saved" for msg in link.messages)


def test_local_clipboard_clear_during_transfer_wins(tmp_path):
    board, link, clipboard = connect(tmp_path)
    ready = receive(clipboard, link)
    revision = board.copy(empty=True)
    clipboard.message(ready)
    assert board.revision() == revision
    assert board.read().empty
    assert not any(msg["op"] == "clip-saved" for msg in link.messages)


def test_completed_files_survive_deskflow_empty_without_echoing(tmp_path):
    board, link, clipboard = connect(tmp_path)
    ready = receive(clipboard, link)
    clipboard.message(ready)
    assert board.read().paths == ready["paths"]
    board.copy(empty=True, engine_owned=True)
    clipboard.poll()
    assert board.read().paths == ready["paths"]
    assert link.offers == []
    assert link.messages.count({"op": "clip-saved", "id": ready["id"]}) == 1
    clipboard.message({"op": "clip-clear", "id": ready["id"]})
    assert board.read().empty


def test_mixed_or_disabled_peer_never_gets_file_offers(tmp_path):
    sample = tmp_path / "clip.mp4"
    sample.write_bytes(b"video")
    for options in ({"peer": False}, {"role": "client", "enabled": False}):
        board, link, clipboard = connect(tmp_path, **options)
        board.copy([sample])
        clipboard.poll()
        assert link.offers == []


def test_only_media_copies_are_advertised_and_next_copy_revokes(tmp_path):
    board, link, clipboard = connect(tmp_path)
    sample = tmp_path / "clip.mp4"
    sample.write_bytes(b"video")
    board.copy([Path("private.pdf"), sample])
    clipboard.poll()
    assert link.offers == []
    board.copy([sample])
    clipboard.poll()
    assert link.offers == [(sample,)]
    board.copy()
    clipboard.poll()
    assert link.revoked == ["a" * 32]


def test_typescript_is_not_automatically_sent_as_video(tmp_path):
    board, link, clipboard = connect(tmp_path)
    for suffix in (".ts", ".mts"):
        source = tmp_path / ("private" + suffix)
        source.write_text("const privateValue = 42", encoding="utf-8")
        board.copy([source])
        clipboard.poll()
    assert link.offers == []
