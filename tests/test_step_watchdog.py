"""A school route that goes quiet has its browsers closed so the next route runs."""

from __future__ import annotations

import threading

import litloop.browser.backend as bb
import litloop.sources as src


def test_quiet_step_closes_its_browsers(monkeypatch):
    closed = []
    monkeypatch.setattr(bb, "close_browsers_since", lambda t0, owner=None: closed.append(owner) or 1)
    monkeypatch.setattr(src, "_user_waiting_for", lambda doi: False)
    monkeypatch.setattr(threading.Event, "wait", lambda self, t=None: self.is_set())  # no 5 s sleeps
    done = threading.Event()
    src._step_watchdog("10.1/x", "WebVPN", done, {"institutional_step_seconds": 0.001})
    assert closed == ["10.1/x"], "the watchdog must close only this paper's browsers"


def test_step_that_finishes_closes_nothing(monkeypatch):
    closed = []
    monkeypatch.setattr(bb, "close_browsers_since", lambda t0, owner=None: closed.append(t0) or 1)
    done = threading.Event()
    done.set()
    src._step_watchdog("10.1/x", "WebVPN", done, {"institutional_step_seconds": 240})
    assert not closed


def test_user_login_extends_the_step(monkeypatch):
    closed = []
    monkeypatch.setattr(bb, "close_browsers_since", lambda t0, owner=None: closed.append(t0) or 1)
    calls = {"n": 0}

    def waiting(doi):
        calls["n"] += 1
        return calls["n"] < 3  # the user is logging in for two checks, then the step finishes

    done = threading.Event()
    monkeypatch.setattr(src, "_user_waiting_for", waiting)
    monkeypatch.setattr(threading.Event, "wait", lambda self, t=None: calls["n"] >= 3)
    src._step_watchdog("10.1/x", "WebVPN", done, {"institutional_step_seconds": 0.001})
    assert not closed
