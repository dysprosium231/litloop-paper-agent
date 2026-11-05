"""Remote assist: a tiny local HTTP server the user pokes from a phone."""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from litloop.institutional.remote_assist import RemoteAssist, _find_free_port


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read().decode()


@pytest.fixture
def assist():
    server = RemoteAssist({"remote_assist_port": 0}, publisher="Elsevier")
    server.start()
    yield server
    server.stop()


def test_a_free_port_is_actually_free():
    port = _find_free_port()
    assert isinstance(port, int) and port > 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_the_urls_point_at_the_port(assist):
    assert assist.url == f"http://localhost:{assist.port}"
    assert assist.lan_url.startswith("http://") and str(assist.port) in assist.lan_url


def test_the_page_names_the_publisher_and_waits(assist):
    status, body = _get(f"{assist.url}/")
    assert status == 200
    assert "Elsevier" in body
    assert "Waiting for verification" in body


def test_status_reports_the_browser_url_and_timeout(assist):
    assist.update_url("https://idp.example.edu/login")
    status, body = _get(f"{assist.url}/api/status")
    payload = json.loads(body)
    assert status == 200
    assert payload["completed"] is False
    assert payload["url"] == "https://idp.example.edu/login"
    assert payload["timeout"] == 300


def test_posting_done_flips_the_state_and_wakes_the_waiter(assist):
    threading.Thread(
        target=lambda: urllib.request.urlopen(
            urllib.request.Request(f"{assist.url}/api/done", data=b"", method="POST"), timeout=5
        ).read(),
        daemon=True,
    ).start()
    assert assist.wait_for_user(timeout=10) is True
    assert json.loads(_get(f"{assist.url}/api/status")[1])["completed"] is True
    assert "Verification complete" in _get(f"{assist.url}/")[1]


def test_waiting_times_out_when_nobody_answers(assist):
    assert assist.wait_for_user(timeout=0.2) is False


def test_unknown_paths_are_404(assist):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(f"{assist.url}/nope")
    assert excinfo.value.code == 404


def test_stopping_twice_is_harmless():
    server = RemoteAssist({"remote_assist_port": 0})
    server.start()
    server.stop()
    server.stop()
