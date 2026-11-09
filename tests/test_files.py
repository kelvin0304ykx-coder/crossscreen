import hashlib
import pytest
from crossscreen.filelink import Incoming, filename


def test_copy_does_not_replace_existing_data(tmp_path):
    (tmp_path / "样本.txt").write_bytes(b"old")
    f = Incoming(tmp_path, "样本.txt", 3)
    f.append(b"new")
    path = f.finish(hashlib.sha256(b"new").hexdigest())
    assert path.name == "样本 (1).txt" and path.read_bytes() == b"new"
    assert (tmp_path / "样本.txt").read_bytes() == b"old"


def test_partial_transfer_has_no_final_file(tmp_path):
    f = Incoming(tmp_path, "transfer.txt", 100)
    f.append(b"partial")
    with pytest.raises(ValueError):
        f.finish(f.hash.hexdigest())
    f.discard()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["../a", "a:b", "CON.txt", "a\\b", "x.", ""])
def test_never_accept_remote_paths(name):
    with pytest.raises(ValueError):
        filename(name)


def test_simultaneous_authenticated_copies(tmp_path):
    import os
    import socket
    import threading
    from crossscreen.config import AppConfig
    from crossscreen.filelink import FileLink
    from crossscreen.security import ensure_identity

    roots = [tmp_path / role for role in ("server", "client")]
    pins = [ensure_identity(root) for root in roots]
    saved = [threading.Event(), threading.Event()]
    links = []
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1] - 1

    def event(i, msg):
        if msg["op"] == "offer":
            links[i].accept(msg["id"], roots[i], msg["count"])
        elif msg["op"] == "saved":
            saved[i].set()

    for i, root in enumerate(roots):
        config = AppConfig(role=root.name, host="127.0.0.1", port=port, peer_fingerprint=pins[1-i])
        links.append(FileLink(config, root / "tls/identity.pem", lambda m, i=i: event(i, m)))
        (root / root.name).write_bytes(os.urandom(512 * 1024 + 17))
    try:
        for link in links:
            link.thread.start()
        assert all(link.connected.wait(6) for link in links)
        for link, root in zip(links, roots):
            assert link.offer([root / root.name])
        assert all(signal.wait(8) for signal in saved)
        for root in roots:
            assert (roots[0] / root.name).read_bytes() == (roots[1] / root.name).read_bytes()
    finally:
        for link in links:
            link.stop()
            link.thread.join(3)


def test_media_clipboard_batches_share_the_authenticated_drag_channel(tmp_path):
    import queue
    import socket
    from crossscreen.config import AppConfig
    from crossscreen.filelink import FileLink
    from crossscreen.security import ensure_identity

    roots = [tmp_path / role for role in ("server", "client")]
    pins = [ensure_identity(root) for root in roots]
    events = [queue.Queue(), queue.Queue()]
    links = []
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1] - 1

    def event(i, msg):
        if msg["op"] in ("offer", "clip-offer"):
            folder = roots[i] / msg["op"]
            folder.mkdir(exist_ok=True)
            links[i].accept(msg["id"], folder, msg["count"],
                            clipboard_size=msg.get("size"))
        elif msg["op"] in ("clip-ready", "saved"):
            events[i].put(msg)

    files = [("截屏.png", b"png" * 90001), ("视频.mp4", b"mp4" * 85003)]
    for i, root in enumerate(roots):
        config = AppConfig(role=root.name, host="127.0.0.1", port=port, peer_fingerprint=pins[1-i])
        links.append(FileLink(config, root / "tls/identity.pem", lambda msg, i=i: event(i, msg)))
        for name, data in files:
            (root / name).write_bytes(data + bytes([i]))
    drag = roots[0] / "原有拖放.txt"
    drag.write_bytes(b"drag")

    try:
        for link in links:
            link.thread.start()
        assert all(link.connected.wait(6) for link in links)
        for link, root in zip(links, roots):
            assert link.offer([root / name for name, _ in files], clipboard=True)
        assert links[0].offer([drag])
        for i, messages in enumerate(events):
            received = [messages.get(timeout=8) for _ in range(2 if i == 0 else 1)]
            ready = next(msg for msg in received if msg["op"] == "clip-ready")
            assert [path.name for path in ready["paths"]] == [name for name, _ in files]
            assert [path.read_bytes() for path in ready["paths"]] == [data + bytes([1-i]) for _, data in files]
        assert (roots[1] / "offer" / drag.name).read_bytes() == b"drag"
    finally:
        for link in links:
            link.stop()
            link.thread.join(3)


@pytest.mark.parametrize("new_content", [b"", b"video that grew during the transfer"])
def test_changing_source_cancels_clipboard_batch_before_end(tmp_path, new_content):
    import json
    from crossscreen.filelink import FileLink

    source = tmp_path / "recording.mp4"
    source.write_bytes(b"video")
    link = FileLink(None, None, lambda _: None)
    link.connected.set()
    link.clipboard_id = "a" * 32
    stream = link.file_frames(link.clipboard_id, ((source, 5),), True)
    kind, payload = next(stream)
    assert kind == b"J" and json.loads(payload)["op"] == "begin"
    source.write_bytes(new_content)
    remaining = list(stream)
    assert sum(len(data) for kind, data in remaining if kind == b"B") <= 5
    assert not any(kind == b"J" and json.loads(data)["op"] == "end" for kind, data in remaining)
    assert link.out.get_nowait()["op"] == "clip-clear"

