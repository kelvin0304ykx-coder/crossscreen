"""Quartz capture and injection. Requires the user's Accessibility approval."""
from .input_geometry import Display
from .input_keys import MACOS, MAC_NAMES

MARKER = 0x4353524E


class MacInput:
    def __init__(self):
        import Quartz as Q
        self.q = Q
        self.started = self.hidden = False
        self.tap = self.run_source = None
        self.keys, self.buttons, self.physical_modifiers = set(), set(), set()
        self.source = Q.CGEventSourceCreate(Q.kCGEventSourceStatePrivate)
        Q.CGEventSourceSetUserData(self.source, MARKER)

    def display(self):
        q = self.q
        _, displays, _ = q.CGGetActiveDisplayList(32, None, None)
        rects = [q.CGDisplayBounds(d) for d in displays]
        left = min(r.origin.x for r in rects)
        top = min(r.origin.y for r in rects)
        right = max(r.origin.x+r.size.width for r in rects)
        bottom = max(r.origin.y+r.size.height for r in rects)
        return Display(round(left), round(top), round(right-left), round(bottom-top))

    def position(self):
        p = self.q.CGEventGetLocation(self.q.CGEventCreate(None))
        return p.x, p.y

    def start(self, capture, fault):
        q = self.q
        self.capture, self.fault = capture, fault
        kinds = (q.kCGEventMouseMoved, q.kCGEventLeftMouseDragged, q.kCGEventRightMouseDragged,
                 q.kCGEventOtherMouseDragged, q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp,
                 q.kCGEventRightMouseDown, q.kCGEventRightMouseUp, q.kCGEventOtherMouseDown,
                 q.kCGEventOtherMouseUp, q.kCGEventScrollWheel, q.kCGEventKeyDown,
                 q.kCGEventKeyUp, q.kCGEventFlagsChanged)
        self.tap = q.CGEventTapCreate(q.kCGHIDEventTap, q.kCGHeadInsertEventTap, q.kCGEventTapOptionDefault,
                                    sum(1 << k for k in kinds), self._event, None)
        if self.tap is None:
            raise OSError('macOS 未允许捕获键鼠，请为跨屏开启辅助功能与输入监控权限')
        self.run_source = q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        q.CFRunLoopAddSource(q.CFRunLoopGetMain(), self.run_source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(self.tap, True)
        self.started = True

    def stop(self):
        q = self.q
        if self.tap:
            q.CGEventTapEnable(self.tap, False)
            q.CFMachPortInvalidate(self.tap)
        if self.run_source:
            q.CFRunLoopRemoveSource(q.CFRunLoopGetMain(), self.run_source, q.kCFRunLoopCommonModes)
        self.tap = self.run_source = None
        self.started = False
        try:
            self.release()
        finally:
            self.capture_mode(False)

    def capture_mode(self, enabled):
        if bool(enabled) != self.hidden:
            q = self.q
            q.CGAssociateMouseAndMouseCursorPosition(not enabled)
            (q.CGDisplayHideCursor if enabled else q.CGDisplayShowCursor)(q.CGMainDisplayID())
            self.hidden = bool(enabled)

    def _event(self, proxy, kind, event, refcon):
        q = self.q
        if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
            self.fault()
            if self.tap:
                q.CGEventTapEnable(self.tap, True)
            return event
        if q.CGEventGetIntegerValueField(event, q.kCGEventSourceUserData) == MARKER:
            return event
        data = None
        try:
            p = q.CGEventGetLocation(event)
            if kind in (q.kCGEventMouseMoved, q.kCGEventLeftMouseDragged,
                        q.kCGEventRightMouseDragged, q.kCGEventOtherMouseDragged):
                data = {'kind':'move', 'dx':q.CGEventGetDoubleValueField(event,q.kCGMouseEventDeltaX),
                        'dy':q.CGEventGetDoubleValueField(event,q.kCGMouseEventDeltaY)}
            elif kind in (q.kCGEventKeyDown,q.kCGEventKeyUp,q.kCGEventFlagsChanged):
                key = MAC_NAMES.get(q.CGEventGetIntegerValueField(event,q.kCGKeyboardEventKeycode))
                down = kind != q.kCGEventKeyUp
                if kind == q.kCGEventFlagsChanged:
                    if key == 'caps_lock':
                        down = bool(q.CGEventGetFlags(event) & q.kCGEventFlagMaskAlphaShift)
                    else:
                        down = key not in self.physical_modifiers
                    (self.physical_modifiers.add if down else self.physical_modifiers.discard)(key)
                data = {'kind':'key','key':key,'down':down}
            elif kind == q.kCGEventScrollWheel:
                continuous = q.CGEventGetIntegerValueField(event,q.kCGScrollWheelEventIsContinuous)
                fields = ((q.kCGScrollWheelEventPointDeltaAxis2,q.kCGScrollWheelEventPointDeltaAxis1)
                          if continuous else (q.kCGScrollWheelEventDeltaAxis2,q.kCGScrollWheelEventDeltaAxis1))
                scale = 1 if continuous else 40
                data = {'kind':'scroll','x':q.CGEventGetDoubleValueField(event,fields[0])*scale,
                        'y':q.CGEventGetDoubleValueField(event,fields[1])*scale}
            else:
                button = q.CGEventGetIntegerValueField(event,q.kCGMouseEventButtonNumber)
                if 0 <= button < 5:
                    data = {'kind':'button','button':button,
                            'down':kind in (q.kCGEventLeftMouseDown,q.kCGEventRightMouseDown,q.kCGEventOtherMouseDown)}
            if data and self.capture(data, (p.x,p.y)):
                return None
        except Exception:
            self.fault()
        return event

    def flags(self):
        q = self.q
        flags = 0
        for prefix, mask in (('shift_',q.kCGEventFlagMaskShift),('ctrl_',q.kCGEventFlagMaskControl),
                             ('alt_',q.kCGEventFlagMaskAlternate),('meta_',q.kCGEventFlagMaskCommand)):
            if any(k.startswith(prefix) for k in self.keys):
                flags |= mask
        return flags

    def post(self, event):
        q = self.q
        if event is None:
            raise OSError('无法创建 macOS 输入事件')
        q.CGEventSetIntegerValueField(event,q.kCGEventSourceUserData,MARKER)
        q.CGEventSetFlags(event,self.flags())
        q.CGEventPost(q.kCGHIDEventTap,event)

    def warp(self, x, y):
        q = self.q
        point = self.display().clamp(x,y)
        if self.buttons:
            button = min(self.buttons)
            kind = (q.kCGEventLeftMouseDragged if button == 0 else
                    q.kCGEventRightMouseDragged if button == 1 else q.kCGEventOtherMouseDragged)
        else:
            button, kind = 0, q.kCGEventMouseMoved
        self.post(q.CGEventCreateMouseEvent(self.source,kind,point,button))

    def inject(self, event):
        q = self.q
        kind = event['kind']
        if kind == 'key':
            key, down = event['key'],event['down']
            (self.keys.add if down else self.keys.discard)(key)
            self.post(q.CGEventCreateKeyboardEvent(self.source,MACOS[key],down))
        elif kind == 'button':
            button, down = event['button'],event['down']
            kinds = ((q.kCGEventLeftMouseUp,q.kCGEventLeftMouseDown),
                     (q.kCGEventRightMouseUp,q.kCGEventRightMouseDown),
                     (q.kCGEventOtherMouseUp,q.kCGEventOtherMouseDown))
            self.post(q.CGEventCreateMouseEvent(self.source,kinds[min(button,2)][down],self.position(),button))
            (self.buttons.add if down else self.buttons.discard)(button)
        elif kind == 'move':
            x,y = self.position()
            self.warp(x+event['dx'],y+event['dy'])
        elif kind == 'scroll':
            self.post(q.CGEventCreateScrollWheelEvent(self.source,q.kCGScrollEventUnitPixel,2,
                                                    round(event['y']),round(event['x'])))

    def release(self):
        try:
            for key in list(self.keys):
                self.inject({'kind':'key','key':key,'down':False})
            for button in list(self.buttons):
                self.inject({'kind':'button','button':button,'down':False})
        finally:
            self.keys.clear()
            self.buttons.clear()
