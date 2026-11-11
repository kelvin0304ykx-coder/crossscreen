"""Duplex input ownership. Pure state machine; never reads or logs user text."""
from collections import deque
import math
import time

from .input_keys import KEYS


def number(value, limit):
    return type(value) in (float, int) and math.isfinite(value) and abs(value) <= limit


def valid_event(event):
    if not isinstance(event, dict):
        return False
    kind = event.get('kind')
    if kind == 'move':
        return set(event) == {'kind', 'dx', 'dy'} and all(number(event.get(k), 32768) for k in ('dx','dy'))
    if kind == 'key':
        return (set(event) == {'kind','key','down'} and isinstance(event.get('key'), str)
                and event['key'] in KEYS and type(event.get('down')) is bool)
    if kind == 'button':
        return (set(event) == {'kind','button','down'} and type(event.get('button')) is int
                and 0 <= event['button'] < 5 and type(event.get('down')) is bool)
    if kind == 'scroll':
        return set(event) == {'kind','x','y'} and all(number(event.get(k), 8192) for k in ('x','y'))
    return False


def crossing(msg):
    ratio = msg.get('ratio')
    held = msg.get('held')
    if (not number(ratio, 1) or ratio < 0 or not isinstance(held, list) or len(held) > 32
            or any(not isinstance(k, str) or k not in KEYS for k in held)):
        raise ValueError('跨屏位置或按键状态无效')
    return ratio, held


class Router:
    """All calls run on the native/Qt event thread. Sender must be nonblocking.

    Server serializes leases, independent of which OS/device supplies input.
    Epochs fence queued packets following handoff, takeover, and return.
    """
    def __init__(self, server, send, action, clock=time.monotonic):
        self.server = server
        self.me = 'server' if server else 'client'
        self.peer = 'client' if server else 'server'
        self.send = send
        self.action = action
        self.clock = clock
        self.connected = False
        self.mode = 'local'
        self.epoch = 0
        self.owner = None
        self.pending = deque()
        self.pending_at = 0
        self.returning = False

    def online(self):
        self.offline()
        self.connected = True
        self.epoch = 0

    def offline(self):
        self.connected = False
        self.owner = None
        self.mode = 'local'
        self.returning = False
        self.pending.clear()
        self.action({'op':'release'})

    def cross(self, ratio, held):
        if not self.connected or self.mode not in ('local', 'receive'):
            return False
        crossing({'ratio':ratio,'held':held})
        self.mode = 'pending'
        self.pending_at = self.clock()
        self.pending.clear()
        self.returning = False
        self.action({'op':'capture'})
        if self.server:
            self._grant(self.me, ratio, held)
        else:
            self.send({'op':'claim', 'ratio':ratio, 'held':held})
        return True

    def _grant(self, owner, ratio, held):
        msg = {'op':'lease','epoch':self.epoch + 1,'owner':owner,'ratio':ratio,'held':held}
        # Enqueue the grant before local input can enqueue any event for it.
        self.send(msg)
        self._lease(msg)

    def _lease(self, msg):
        ratio, held = crossing(msg)
        epoch = msg.get('epoch')
        owner = msg.get('owner')
        if type(epoch) is not int or epoch <= 0 or owner not in ('server','client',None):
            raise ValueError('控制权消息无效')
        if epoch <= self.epoch:
            return
        old_owner = self.owner
        old_mode = self.mode
        if old_mode == 'pending' and owner != self.me:
            # Acknowledgement of an earlier return/claim can arrive while our
            # subsequent claim is in flight. Keep its buffered key-ups intact.
            self.epoch, self.owner = epoch, owner
            self.action({'op':'release'})
            self.action({'op':'capture'})
            return
        waiting = list(self.pending) if old_mode == 'pending' and owner == self.me else []
        self.pending.clear()
        self.epoch = epoch
        self.owner = owner
        self.returning = False
        self.action({'op':'release'})
        if owner is None:
            self.mode = 'local'
            if old_owner == self.me:
                self.action({'op':'restore', 'ratio':ratio})
        elif owner == self.me:
            self.mode = 'send'
            self.action({'op':'capture'})
            self.action({'op':'source_release', 'held':held})
            for event in waiting:
                self.input(event)
        else:
            self.mode = 'receive'
            self.action({'op':'enter','ratio':ratio,'held':held})

    def input(self, event):
        if not self.connected or self.mode not in ('pending','send'):
            return False
        if not valid_event(event):
            raise ValueError('输入消息无效')
        if self.mode == 'pending':
            if len(self.pending) >= 128 or self.clock() - self.pending_at > .6:
                raise TimeoutError('跨屏确认超时')
            self.pending.append(event)
        else:
            self.send({'op':'input','epoch':self.epoch,'event':event})
        return True

    def return_home(self, ratio=.5):
        if not self.connected or self.owner is None or self.returning:
            return
        self.returning = True
        if self.server:
            self._grant(None, ratio, [])
        else:
            self.send({'op':'return','epoch':self.epoch,'ratio':ratio})

    def take_local(self):
        if self.mode != 'local':
            self.return_home()
            self.mode = 'local'
            self.pending.clear()
            self.action({'op':'release'})

    def tick(self):
        if self.mode == 'pending' and self.clock() - self.pending_at > .6:
            raise TimeoutError('跨屏确认超时')

    def receive(self, msg):
        if not self.connected:
            return
        if not isinstance(msg, dict):
            raise ValueError('输入消息格式错误')
        op = msg.get('op')
        if op == 'claim' and self.server:
            ratio, held = crossing(msg)
            self._grant(self.peer, ratio, held)
        elif op == 'lease' and not self.server:
            self._lease(msg)
        elif op == 'return' and self.server:
            if msg.get('epoch') == self.epoch:
                ratio, _ = crossing({'ratio':msg.get('ratio'),'held':[]})
                self._grant(None, ratio, [])
        elif op == 'input':
            if not valid_event(msg.get('event')):
                raise ValueError('输入消息无效')
            if self.mode == 'receive' and not self.returning and msg.get('epoch') == self.epoch:
                self.action({'op':'inject','event':msg['event']})
        else:
            raise ValueError('未知输入协议消息')
