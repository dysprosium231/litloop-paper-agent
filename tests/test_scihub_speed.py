"""Tests for Sci-Hub speed measurement and route selection."""

from __future__ import annotations

import time
import pytest

import litloop.config
import litloop.network
from litloop.sources import scihub_speed


@pytest.fixture(autouse=True)
def setup_scihub_speed(monkeypatch, tmp_path):
    """Set DATA_DIR to tmp_path, enable proxy reachability, and reset speed state."""
    monkeypatch.setattr(litloop.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(litloop.network, "_proxy_reachable", lambda url: True)
    scihub_speed.forget()
    yield
    scihub_speed.forget()


def test_mode():
    """Verify route mode parsing for auto, default direct, and legacy proxy."""
    assert scihub_speed.mode({"scihub_route": "auto"}) == "auto"
    assert scihub_speed.mode({}) == "direct"
    assert scihub_speed.mode({"scihub_via_proxy": True}) == "proxy"


def test_measure_auto_selects_fastest(monkeypatch):
    """Verify measure selects the fastest mirror/route and updates network routing."""
    config = {
        "scihub_route": "auto",
        "host_proxy_url": "http://127.0.0.1:10808",
        "scihub_domains": ["https://sci-hub.vg", "https://sci-hub.ru"],
    }

    def fake_one(doi, mirror, rt, cfg, tmp):
        # measure() keeps the first to finish, so the fake takes real time
        dt = 2.0 if (mirror == "https://sci-hub.ru" and rt == "direct") else 10.0
        time.sleep(dt / 20)
        return mirror, rt, dt, 1024 * 1024

    monkeypatch.setattr(scihub_speed, "_one", fake_one)

    result = scihub_speed.measure(config)
    assert result is not None
    assert result["mirror"] == "https://sci-hub.ru"
    assert result["route"] == "direct"
    assert scihub_speed.route(config) == "direct"
    assert scihub_speed.preferred_mirror() == "https://sci-hub.ru"
    assert litloop.network.grey_proxy(config) == ""


def test_measure_proxy_only_evaluates_proxy_routes(monkeypatch):
    """Verify proxy mode only measures proxy routes without touching direct."""
    config = {
        "scihub_route": "proxy",
        "host_proxy_url": "http://127.0.0.1:10808",
        "scihub_domains": ["https://sci-hub.vg", "https://sci-hub.ru"],
    }
    tested_routes = []

    def fake_one(doi, mirror, rt, cfg, tmp):
        tested_routes.append(rt)
        return mirror, rt, 2.0, 1024 * 1024

    monkeypatch.setattr(scihub_speed, "_one", fake_one)

    scihub_speed.measure(config)
    assert tested_routes
    assert all(rt != "direct" for rt in tested_routes)
    assert "direct" not in tested_routes


def test_measure_direct_skips_measurement():
    """Verify direct route skips speed measurement and stays direct."""
    config = {"scihub_route": "direct"}
    assert scihub_speed.measure(config) is None
    assert scihub_speed.route(config) == "direct"


def test_note_result_drops_after_three_failures(monkeypatch):
    """Verify consecutive failures drop chosen mirror while absent does not count."""
    config = {
        "scihub_route": "auto",
        "host_proxy_url": "http://127.0.0.1:10808",
        "scihub_domains": ["https://sci-hub.vg", "https://sci-hub.ru"],
    }

    def fake_one(doi, mirror, rt, cfg, tmp):
        if mirror == "https://sci-hub.ru" and rt == "direct":
            return mirror, rt, 2.0, 1024 * 1024
        return mirror, rt, float("inf"), 0

    monkeypatch.setattr(scihub_speed, "_one", fake_one)

    res = scihub_speed.measure(config)
    assert res is not None
    mirror = res["mirror"]
    assert scihub_speed.current() is not None

    for _ in range(3):
        scihub_speed.note_result(mirror, False, absent=True)
    assert scihub_speed.current() is not None

    scihub_speed.note_result(mirror, False)
    assert scihub_speed.current() is not None
    scihub_speed.note_result(mirror, False)
    assert scihub_speed.current() is not None
    scihub_speed.note_result(mirror, False)
    assert scihub_speed.current() is None


def _order_of_events(monkeypatch, tmp_path, fastest):
    import threading
    from litloop.sources import scihub, scihub_speed
    events = []
    probe_done = threading.Event()

    def probe(config):
        events.append(("probe", threading.current_thread().name))
        probe_done.wait(0.5)

    def domain(doi, d, out, config, use_tor=False, fail_notes=None):
        events.append(("try", d))
        return None

    monkeypatch.setattr(scihub, "_probe_scihub_domains", probe)
    monkeypatch.setattr(scihub, "try_scihub_domain", domain)
    monkeypatch.setattr(scihub_speed, "preferred_mirror", lambda: fastest)
    monkeypatch.setattr(scihub, "_wall_guard", lambda d, config: False)
    monkeypatch.setattr(scihub, "load_stats", lambda config: {})
    monkeypatch.setattr(scihub, "record_result", lambda *a, **k: None)
    cfg = {"scihub_enabled": True, "scihub_domains": ["https://sci-hub.vg", "https://sci-hub.st"]}
    scihub._try_scihub_impl("10.9999/probe", tmp_path / "x.pdf", cfg)
    probe_done.set()
    return events


def test_a_fresh_speed_test_starts_the_paper_without_waiting_for_the_probe(monkeypatch, tmp_path):
    events = _order_of_events(monkeypatch, tmp_path, "https://sci-hub.vg")
    tries = [e for e in events if e[0] == "try"]
    assert tries and tries[0] == ("try", "https://sci-hub.vg")
    probes = [e for e in events if e[0] == "probe"]
    assert all(name == "scihub-probe" for _, name in probes)  # off the paper's thread


def test_without_a_speed_test_the_probe_comes_first(monkeypatch, tmp_path):
    import threading
    events = _order_of_events(monkeypatch, tmp_path, None)
    assert events[0] == ("probe", threading.current_thread().name)
