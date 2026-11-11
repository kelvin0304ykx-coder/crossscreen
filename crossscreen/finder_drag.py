"""Pick up an active Finder drag when the shared pointer actually leaves macOS."""
from pathlib import Path


def finish_handoff(device, defer, still_away):
    # After leaving macOS, the USB up is routed to Windows. Cancel the
    # *source* drag before lifting its synthetic press (never drop locally).
    original = device.signature()
    if original is None:
        return
    device.cancel_finder(original[0])
    def lift_if_same():
        if still_away() and device.signature() == original:
            device.lift()
    defer(lift_if_same)


class FinderInput:
    def __init__(self, marker=None):
        self.marker = marker

    def mark(self, event):
        if self.marker is not None:
            import Quartz as q
            q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, self.marker)

    def signature(self):
        from AppKit import NSWorkspace
        import Quartz as q
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if (app and app.bundleIdentifier() == "com.apple.finder"
                and q.CGEventSourceButtonState(0, q.kCGMouseButtonLeft)):
            return (app.processIdentifier(),
                    q.CGEventSourceCounterForEventType(0, q.kCGEventLeftMouseDown))

    def cancel_finder(self, pid):
        import Quartz as q
        for down in (True, False):
            key = q.CGEventCreateKeyboardEvent(None, 53, down)  # Escape
            q.CGEventSetFlags(key, 0)
            self.mark(key)
            q.CGEventPostToPid(pid, key)

    def lift(self):
        import Quartz as q
        point = q.CGEventGetLocation(q.CGEventCreate(None))
        up = q.CGEventCreateMouseEvent(None, q.kCGEventLeftMouseUp, point, q.kCGMouseButtonLeft)
        q.CGEventSetFlags(up, 0)
        self.mark(up)
        q.CGEventPost(q.kCGHIDEventTap, up)


class FinderGesture:
    def __init__(self):
        self.baseline = None

    def enter(self, revision):
        self.baseline = revision

    def sample(self, revision, down):
        if self.baseline is not None and not down:
            self.baseline = revision

    def take(self, revision, down):
        baseline, self.baseline = self.baseline, None
        return bool(down and baseline is not None and revision != baseline)


class FinderDragBoard:
    def __init__(self):
        from AppKit import NSPasteboard, NSPasteboardNameDrag
        self.board = NSPasteboard.pasteboardWithName_(NSPasteboardNameDrag)

    def revision(self):
        return self.board.changeCount()

    def files(self):
        from AppKit import NSPasteboardTypeFileURL, NSWorkspace
        from Foundation import NSURL
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not front or front.bundleIdentifier() != 'com.apple.finder':
            return []
        items = self.board.pasteboardItems() or []
        if not 1 <= len(items) <= 64:
            return []
        paths = []
        for item in items:
            raw = item.stringForType_(NSPasteboardTypeFileURL)
            url = NSURL.URLWithString_(raw) if raw else None
            if not url or not url.isFileURL() or url.host() not in (None, '', 'localhost'):
                return []
            # Finder may vend a file-reference URL (/.file/id=...), not a path.
            resolved = url.filePathURL()
            if not resolved or not resolved.path():
                return []
            paths.append(Path(resolved.path()))
        return paths
