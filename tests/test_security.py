import json
import pytest


def test_identity_persists_and_pairing_never_contains_private_key(tmp_path):
    from crossscreen.security import ensure_identity, make_pairing, parse_pairing
    first = ensure_identity(tmp_path)
    assert ensure_identity(tmp_path) == first
    assert len(first) == 64
    text = make_pairing("windows", "192.168.1.8", 24870, first, "server")
    data = parse_pairing(text)
    assert data["fingerprint"] == first
    assert "PRIVATE" not in text
    assert set(data) == {"version", "name", "host", "port", "fingerprint", "role"}
    assert "PRIVATE KEY" in (tmp_path / "tls" / "identity.pem").read_text()


def test_identity_is_unique_per_device(tmp_path):
    from crossscreen.security import ensure_identity
    assert ensure_identity(tmp_path / "one") != ensure_identity(tmp_path / "two")


@pytest.mark.parametrize("changes", [{"fingerprint": "0"}, {"name": "mac\n[security]"}, {"role": "anything"}, {"port": 0}, {"private_key": "do not accept"}])
def test_pairing_rejects_invalid_or_extra_fields(changes):
    from crossscreen.security import parse_pairing
    data = dict(version=1, name="mac", host="192.168.1.9", port=24870, fingerprint="ab" * 32, role="client")
    data.update(changes)
    with pytest.raises(ValueError):
        parse_pairing(json.dumps(data))


def test_trust_uses_sha256_and_only_accepts_the_supplied_peer(tmp_path):
    from crossscreen.security import trust_peer
    trust_peer(tmp_path, "ab" * 32, "server")
    text = (tmp_path / "tls" / "trusted-servers").read_text()
    assert text == "v2:sha256:" + "ab" * 32 + "\n"
    assert not (tmp_path / "tls" / "trusted-clients").exists()
