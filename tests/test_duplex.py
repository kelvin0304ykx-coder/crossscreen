from collections import deque
import pytest


def pair():
    from crossscreen.duplex import Router
    pending = deque()
    actions = [[], []]
    routers = [Router(i == 0, lambda m, i=i: pending.append((1-i, m)),
                      actions[i].append) for i in range(2)]
    def flush():
        while pending:
            i, message = pending.popleft()
            routers[i].receive(message)
    for r in routers:
        r.online()
    flush()
    return routers, actions, pending, flush


@pytest.mark.parametrize('source', [0, 1])
def test_both_keyboards_follow_their_own_mouse_and_return(source):
    rs, actions, _, flush = pair()
    rs[source].cross(.4, [])
    flush()
    assert rs[source].mode == 'send'
    assert rs[1-source].mode == 'receive'
    rs[source].input({'kind': 'key', 'key': 'a', 'down': True})
    flush()
    assert actions[1-source][-1]['event'] == {'kind':'key', 'key':'a', 'down':True}
    rs[1-source].return_home(.7)
    flush()
    assert all(r.mode == 'local' for r in rs)
    assert any(a['op'] == 'restore' for a in actions[source])
    assert actions[1-source][-1]['op'] == 'release'


def test_alternating_physical_sets_release_old_keys_and_drop_late_packets():
    rs, actions, _, flush = pair()
    rs[0].cross(.5, [])
    flush()
    old = rs[0].epoch
    rs[1].take_local()
    rs[1].cross(.2, [])
    flush()
    assert rs[1].mode == 'send' and rs[0].mode == 'receive'
    before = list(actions[1])
    rs[1].receive({'op':'input', 'epoch':old, 'event':{'kind':'key','key':'a','down':True}})
    assert actions[1] == before


def test_simultaneous_requests_have_one_owner():
    rs, _, _, flush = pair()
    rs[0].cross(.5, [])
    rs[1].cross(.5, [])
    flush()
    assert sorted(r.mode for r in rs) == ['receive', 'send']
    assert rs[0].epoch == rs[1].epoch


def test_disconnect_and_reconnect_never_replay_keys():
    rs, actions, pending, flush = pair()
    rs[1].cross(.4, [])
    flush()
    rs[1].input({'kind':'key','key':'shift_l','down':True})
    flush()
    for r in rs:
        r.offline()
    assert all(r.mode == 'local' and not r.connected for r in rs)
    assert all(a[-1]['op'] == 'release' for a in actions)
    pending.clear()
    for r in rs:
        r.online()
    flush()
    assert all(r.mode == 'local' for r in rs)


def test_invalid_event_cannot_reach_native_backend():
    from crossscreen.duplex import valid_event
    for data in ({'kind':'key','key':'exec','down':True}, {'kind':'move','dx':float('nan'),'dy':0},
                 {'kind':'button','button':20,'down':1}, {'kind':'move','dx':1000000,'dy':0}):
        assert not valid_event(data)
    assert valid_event({'kind':'move','dx':12,'dy':-4})


def test_no_input_is_suppressed_when_not_connected():
    rs, _, _, _ = pair()
    rs[0].offline()
    assert not rs[0].cross(.5, [])
    assert not rs[0].input({'kind':'key','key':'a','down':True})


def test_source_cannot_forge_server_lease():
    rs, _, _, _ = pair()
    with pytest.raises(ValueError):
        rs[0].receive({'op':'lease', 'epoch':12, 'owner':'client','ratio':.5,'held':[]})


def test_pending_crossing_buffers_and_flushes_key_up():
    rs, actions, _, flush = pair()
    rs[1].cross(.5, ['shift_l'])
    assert rs[1].input({'kind':'key','key':'shift_l','down':False})
    flush()
    assert actions[0][-1]['event']['down'] is False


def test_real_tls_transport_requires_paired_certificates_and_delivers_in_order(tmp_path):
    import queue
    import socket
    import time
    from crossscreen.input_link import InputLink
    from crossscreen.config import AppConfig
    from crossscreen.security import ensure_identity
    roots = [tmp_path / str(i) for i in range(2)]
    pins = [ensure_identity(root) for root in roots]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    queues = [queue.Queue(), queue.Queue()]
    links = [InputLink(AppConfig(role=role, host='127.0.0.1', port=port, peer_fingerprint=pins[1-i]),
                       roots[i] / 'tls/identity.pem', queues[i].put_nowait)
             for i, role in enumerate(('server','client'))]
    def until(i, op):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            msg = queues[i].get(timeout=6)
            if msg.get('op') == op:
                return msg
        raise AssertionError(op)
    try:
        for link in links:
            link.start()
        for i in range(2):
            assert until(i, 'online')['side'] == 'left'
        for i in range(2):
            for n in range(25):
                links[i].send({'op':'input','epoch':n,'event':{'kind':'move','dx':i+1,'dy':0}})
        for i in range(2):
            assert [until(i, 'input')['epoch'] for _ in range(25)] == list(range(25))
    finally:
        for link in links:
            link.stop()
    assert all(not link.thread.is_alive() for link in links)


def test_input_link_rejects_overlong_packet_without_allocating_payload():
    from crossscreen.input_link import decode_frames, MAX_FRAME
    import struct
    with pytest.raises(ValueError):
        decode_frames(bytearray(struct.pack('!I', MAX_FRAME + 1)))


def test_physical_key_maps_roundtrip_without_collision():
    from crossscreen.input_keys import WINDOWS, MACOS, WIN_NAMES, MAC_NAMES
    assert len(WINDOWS) == len(WIN_NAMES) == len(MACOS) == len(MAC_NAMES)
    assert WIN_NAMES[0x11d] == MAC_NAMES[62] == 'ctrl_r'


def test_late_return_cannot_cancel_new_source():
    rs, _, _, flush = pair()
    rs[0].cross(.5, [])
    flush()
    old = rs[0].epoch
    rs[1].cross(.6, [])
    flush()
    rs[0].receive({'op':'return','epoch':old,'ratio':.2})
    flush()
    assert rs[1].mode == 'send'


def test_takeover_claim_keeps_pending_key_release_across_return_ack():
    rs, actions, _, flush = pair()
    rs[0].cross(.5, [])
    flush()
    rs[1].take_local()
    rs[1].cross(.5, ['shift_l'])
    rs[1].input({'kind':'key','key':'shift_l','down':False})
    flush()
    assert actions[0][-1] == {'op':'inject','event':{'kind':'key','key':'shift_l','down':False}}


@pytest.mark.parametrize('event', [None, [], {'kind':'key','key':[], 'down':True},
                                 {'kind':'button','button':True,'down':True}])
def test_malformed_event_is_rejected_without_type_errors(event):
    from crossscreen.duplex import valid_event
    assert not valid_event(event)
