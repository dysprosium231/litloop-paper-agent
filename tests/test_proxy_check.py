"""Telling apart proxy programs in TUN mode (needs direct rules) from system-proxy mode (bypassed)."""

from litloop.institutional import proxy_check


def facts(adapters=(), processes=(), enable=0, server=None):
    return {"adapters": [{"Name": n, "InterfaceDescription": d} for n, d in adapters],
            "processes": list(processes), "proxy_enable": enable, "proxy_server": server}


def test_v2rayn_in_tun_mode_as_seen_on_2026_10_16():
    got = proxy_check.detect(facts(
        adapters=[("singbox_tun", "sing-tun Tunnel"), ("ZeroTier One [166359304edb3c99]", "ZeroTier Virtual Port #2"),
                  ("WLAN", "Intel(R) Wi-Fi 6E AX211 160MHz")],
        processes=["v2rayN", "sing-box", "explorer"], enable=1, server="127.0.0.1:10808"))
    assert got["mode"] == "tun" and got["tun"] == ["singbox_tun"]   # ZeroTier is not a proxy
    assert got["programs"] == ["v2rayN", "sing-box"] and got["system_proxy"] == "127.0.0.1:10808"
    assert "litloop-chrome.exe" in got["direct_processes"]


def test_system_proxy_only_needs_nothing():
    got = proxy_check.detect(facts(adapters=[("WLAN", "Wi-Fi")], processes=["clash-verge", "verge-mihomo"],
                                   enable=1, server="127.0.0.1:7897"))
    assert got["mode"] == "system" and got["programs"] == ["Clash Verge"]


def test_clash_tun_adapter_by_description():
    got = proxy_check.detect(facts(adapters=[("Meta", "Meta Tunnel")], processes=["verge-mihomo"]))
    assert got["mode"] == "tun"


def test_nothing_running():
    assert proxy_check.detect(facts(adapters=[("Ethernet", "Realtek PCIe GbE")], processes=["explorer"]))["mode"] == "none"


def test_network_check_gives_direct_rules_for_tun_mode(monkeypatch, tmp_path):
    from litloop import config
    from litloop.desktop import settings as app_settings
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(app_settings, "_egress_checks", lambda cfg: [])  # no network in tests
    monkeypatch.setattr(proxy_check, "detect", lambda: {
        "mode": "tun", "programs": ["Clash Verge"], "tun": ["Meta"], "system_proxy": None,
        "direct_processes": ["python.exe", "litloop-chrome.exe"]})
    monkeypatch.setattr(proxy_check, "litloop_paths", lambda: ["C:/Python/python.exe"])
    (r,) = app_settings.test_network()
    assert r["ok"] is False and "TUN" in r["detail"]
    (guide,) = r["guides"]
    assert guide["title"].endswith("Clash Verge 中设置直连")
    assert "PROCESS-NAME,litloop-chrome.exe,DIRECT" in guide["copy"][0]["lines"]


def test_network_check_system_proxy_is_fine(monkeypatch, tmp_path):
    from litloop import config
    from litloop.desktop import settings as app_settings
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(app_settings, "_egress_checks", lambda cfg: [])  # no network in tests
    monkeypatch.setattr(proxy_check, "detect", lambda: {
        "mode": "system", "programs": ["v2rayN"], "tun": [], "system_proxy": "127.0.0.1:10808",
        "direct_processes": ["python.exe"]})
    (r,) = app_settings.test_network()
    assert r["ok"] is True and "guides" not in r


def test_egress_checks_report_the_exit_and_the_publishers_answer(monkeypatch):
    import requests

    from litloop.desktop import settings as app_settings
    from litloop.sources import elsevier_check

    class Resp:
        def json(self):
            return {"ip": "219.143.23.107", "city": "Beijing", "country": "CN", "org": "AS4847 China Networks"}
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: Resp())
    monkeypatch.setattr(elsevier_check, "_head_probe", lambda doi, key, proxies: (403, elsevier_check.NOT_ENTITLED))
    ip, right = app_settings._egress_checks({"elsevier_api_key": "k"})
    assert "219.143.23.107" in ip["detail"] and "AS4847" in ip["detail"]
    assert right["ok"] is None and "没有全文权限" in right["detail"]  # a notice, not a failure
    monkeypatch.setattr(elsevier_check, "_head_probe", lambda doi, key, proxies: (200, elsevier_check.ENTITLED))
    assert app_settings._egress_checks({"elsevier_api_key": "k"})[1]["ok"] is True
    assert app_settings._egress_checks({})[1]["ok"] is None  # no key: cannot tell
