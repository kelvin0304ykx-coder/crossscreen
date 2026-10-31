"""Own one input engine; EOF on the GUI pipe always stops that engine."""
import os
import signal
import subprocess
import sys
import threading


def worker_main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, creationflags=creation)
    stopping = threading.Event()

    def stop_child(*_):
        if stopping.is_set():
            return
        stopping.set()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()

    def watch_owner():
        try:
            sys.stdin.buffer.read(1)
        finally:
            stop_child()

    signal.signal(signal.SIGTERM, stop_child)
    signal.signal(signal.SIGINT, stop_child)
    threading.Thread(target=watch_owner, daemon=True).start()
    try:
        for raw in iter(child.stdout.readline, b""):
            try:
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
            except (BrokenPipeError, OSError):
                stop_child()
                break
        result = child.wait(timeout=4)
        return 0 if stopping.is_set() else result
    finally:
        stop_child()


if __name__ == "__main__":
    raise SystemExit(worker_main())
