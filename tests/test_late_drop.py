from pathlib import Path


def test_late_offer_uses_folder_from_same_physical_release_only():
    from crossscreen.drag import HostPress
    h = HostPress()
    a = h.begin(True)
    h.defer(Path("first"), 100)
    assert h.take("old", 100.1) is None
    assert h.take(a, 100.1) == Path("first")
    assert h.take(a, 100.11) is None
    b = h.begin(True)
    h.defer(Path("other"), 200)
    c = h.begin(True)
    assert c != b and h.take(b, 200.1) is None
    h.defer(Path("expired"), 300)
    assert h.take(c, 302) is None
