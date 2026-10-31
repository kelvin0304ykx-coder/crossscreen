import json
import pytest


def test_layout_is_reciprocal_on_either_side():
    from crossscreen.config import render_layout
    for side, opposite in [("right", "left"), ("left", "right")]:
        text = render_layout("windows", "mac", side)
        assert f"{side} = mac" in text
        assert f"{opposite} = windows" in text
        assert "switchToScreen(windows)" in text
        assert "switchToScreen(mac)" in text


@pytest.mark.parametrize("bad", ["mac\nend", "../mac", "mac; x", "", "a" * 65])
def test_layout_rejects_config_injection(bad):
    from crossscreen.config import render_layout
    with pytest.raises(ValueError):
        render_layout("windows", bad, "right")


def test_settings_use_tls_pin_peers_and_configurable_clipboard(tmp_path):
    from crossscreen.config import AppConfig, write_engine_settings
    c = AppConfig(role="server", local_name="windows", peer_name="mac", host="127.0.0.1")
    path = write_engine_settings(c, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "tlsEnabled=true" in text
    assert "checkPeerFingerprints=true" in text
    assert "enableClipboard=true" in text
    c.clipboard = False
    assert "enableClipboard=false" in write_engine_settings(c, tmp_path).read_text(encoding="utf-8")
    assert "toFile=false" in text
    assert "computerName=windows" in text
    assert "127.0.0.1" in text


@pytest.mark.parametrize("host", ["x\n[security]\ntlsEnabled=false", "-s", "localhost:1234", "x y", ""])
def test_rejects_bad_host(host):
    from crossscreen.config import AppConfig
    with pytest.raises(ValueError):
        AppConfig(role="client", local_name="mac", peer_name="windows", host=host).validate()


def test_config_roundtrip(tmp_path):
    from crossscreen.config import AppConfig
    c = AppConfig(role="client", local_name="mac", peer_name="windows", host="192.168.1.8", side="left")
    c.save(tmp_path / "app.json")
    assert AppConfig.load(tmp_path / "app.json") == c


def test_default_address_prefers_real_lan_over_vpn_and_wsl():
    from crossscreen.config import select_lan_address
    addresses = [("TestVPN", "198.18.0.1"), ("vEthernet (WSL)", "10.123.1.1"),
                 ("Tailscale", "100.100.1.2"), ("WLAN", "10.23.45.67")]
    assert select_lan_address(addresses) == "10.23.45.67"
