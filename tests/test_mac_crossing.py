def test_client_output_reports_actual_screen_exit_but_server_does_not_reverse_it():
    from crossscreen.engine import status_from_line
    assert status_from_line('INFO: entering computer', client=True)[0] == 'mac'
    assert status_from_line('INFO: leaving computer', client=True)[0] == 'windows'
    assert status_from_line('INFO: leaving computer') is None


def test_fast_finder_drag_without_an_edge_event_offers_only_current_gesture():
    from crossscreen.finder_drag import FinderGesture
    g = FinderGesture()
    g.enter(17)
    g.sample(18, down=False)  # A previous local drag finished here.
    assert not g.take(18, down=True)
    g.enter(18)
    # One long movement crosses without any intermediate edge samples.
    assert g.take(19, down=True)
    assert not g.take(19, down=True)
    g.enter(19)
    assert not g.take(20, down=False)
    assert not g.take(21, down=True)  # Outside the shared Mac session.


def test_completed_finder_handoff_releases_only_its_own_press():
    from crossscreen.finder_drag import finish_handoff
    class Input:
        current = (17, 92)
        released = 0
        def signature(self):
            return self.current
        def cancel_finder(self, pid):
            self.cancelled = pid
        def lift(self):
            self.released += 1
    device = Input()
    delayed = []
    finish_handoff(device, delayed.append, lambda: True)
    assert device.cancelled == 17 and device.released == 0
    delayed.pop()()
    assert device.released == 1
    finish_handoff(device, delayed.append, lambda: True)
    device.current = (17, 93)  # A new local mouse-down belongs to someone else.
    delayed.pop()()
    assert device.released == 1
    finish_handoff(device, delayed.append, lambda: False)
    delayed.pop()()
    assert device.released == 1
