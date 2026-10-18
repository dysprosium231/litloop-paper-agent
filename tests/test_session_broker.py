"""Tests for long-lived session broker in litloop.session_broker."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import pytest

from litloop.session_broker import (
    BrokerState,
    broker_dir,
    broker_is_running,
    broker_key,
    broker_state_path,
    broker_stop_path,
    load_broker_state,
    pid_is_running,
    start_broker_process,
    submit_broker_job,
    write_broker_state,
)


@pytest.fixture(autouse=True)
def _isolate_broker_root(tmp_path: Path, monkeypatch):
    """Ensure broker directory operations stay within tmp_path."""
    root = tmp_path / "brokers"
    monkeypatch.setattr("litloop.session_broker.BROKER_ROOT", root)
    return root


def test_broker_key_sanitization():
    assert broker_key("elsevier") == "elsevier"
    assert broker_key("Science Direct") == "science-direct"
    assert broker_key("Wiley & Sons / IEEE") == "wiley---sons---ieee"
    assert broker_key("Springer_Nature-2026") == "springer_nature-2026"


def test_broker_paths():
    b_dir = broker_dir("elsevier")
    assert b_dir.name == "elsevier"
    assert broker_state_path("elsevier") == b_dir / "state.json"
    assert broker_stop_path("elsevier") == b_dir / "stop"


def test_load_and_write_broker_state(tmp_path: Path):
    assert load_broker_state("elsevier") is None

    state = BrokerState(
        publisher="elsevier",
        profile_dir=str(tmp_path / "profile"),
        pid=12345,
        queue_dir=str(tmp_path / "queue"),
        started_at="2026-10-18T10:00:00",
        ttl_seconds=3600,
        heartbeat_at="2026-10-18T10:05:00",
    )
    write_broker_state(state)

    loaded = load_broker_state("elsevier")
    assert loaded is not None
    assert loaded["publisher"] == "elsevier"
    assert loaded["pid"] == 12345
    assert loaded["ttl_seconds"] == 3600
    assert loaded["heartbeat_at"] == "2026-10-18T10:05:00"


def test_load_broker_state_corrupt_file():
    path = broker_state_path("elsevier")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupt-json", encoding="utf-8")

    assert load_broker_state("elsevier") is None


def test_pid_is_running(monkeypatch):
    assert pid_is_running(0) is False
    assert pid_is_running(-1) is False

    # Simulate running pid
    monkeypatch.setattr("os.kill", lambda pid, sig: None)
    assert pid_is_running(9999) is True

    # Simulate dead pid
    def fake_kill(pid, sig):
        raise OSError("Process does not exist")

    monkeypatch.setattr("os.kill", fake_kill)
    assert pid_is_running(9999) is False


def test_broker_is_running(monkeypatch, tmp_path: Path):
    # No state on disk
    assert broker_is_running("elsevier") is False

    # Write state
    state = BrokerState(
        publisher="elsevier",
        profile_dir=str(tmp_path / "profile"),
        pid=5555,
        queue_dir=str(tmp_path / "queue"),
        started_at="2026-10-18T10:00:00",
        ttl_seconds=3600,
    )
    write_broker_state(state)

    # Dead process
    monkeypatch.setattr("litloop.session_broker.pid_is_running", lambda pid: False)
    assert broker_is_running("elsevier") is False

    # Alive process
    monkeypatch.setattr("litloop.session_broker.pid_is_running", lambda pid: pid == 5555)
    assert broker_is_running("elsevier") is True


def test_start_broker_process_unlinks_stop_and_spawns(monkeypatch, tmp_path: Path):
    # Create stop file
    stop = broker_stop_path("elsevier")
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("stop", encoding="utf-8")
    assert stop.exists()

    launched_args = []

    class DummyPopen:
        def __init__(self, args, **kwargs):
            launched_args.extend(args)

    monkeypatch.setattr("subprocess.Popen", DummyPopen)

    start_broker_process(
        publisher="elsevier",
        browser_profile=str(tmp_path / "profile"),
        institution="Tsinghua",
        ttl_seconds=1800,
        cwd=tmp_path,
    )

    assert not stop.exists(), "start_broker_process should clear existing stop file"
    assert "session-broker-run" in launched_args
    assert "--publisher" in launched_args
    assert "elsevier" in launched_args
    assert "--ttl" in launched_args
    assert "1800" in launched_args


def test_submit_broker_job_without_state_raises():
    with pytest.raises(RuntimeError, match="No broker state for elsevier"):
        submit_broker_job(
            publisher="elsevier",
            records=[],
            output_dir="out",
            institution="Tsinghua",
            login_timeout=10,
            pdf_timeout=10,
            post_login_hold=0,
            post_run_hold=0,
            timeout_seconds=5,
        )


def test_submit_broker_job_success(tmp_path: Path, monkeypatch):
    queue_dir = tmp_path / "queue"
    state = BrokerState(
        publisher="elsevier",
        profile_dir=str(tmp_path / "profile"),
        pid=1234,
        queue_dir=str(queue_dir),
        started_at="2026-10-18T10:00:00",
        ttl_seconds=3600,
    )
    write_broker_state(state)
    monkeypatch.setattr("litloop.session_broker.broker_is_running", lambda pub: True)

    # When submit_broker_job sleeps, create the .done.json result file
    def fake_sleep(duration):
        for job_file in queue_dir.glob("*.json"):
            if not job_file.name.endswith(".done.json"):
                done_path = queue_dir / f"{job_file.stem}.done.json"
                done_path.write_text(
                    json.dumps({"success": True, "downloaded": 1}, ensure_ascii=False),
                    encoding="utf-8",
                )

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = submit_broker_job(
        publisher="elsevier",
        records=[{"doi": "10.1016/j.test.2026.01"}],
        output_dir=str(tmp_path / "out"),
        institution="Tsinghua",
        login_timeout=10,
        pdf_timeout=10,
        post_login_hold=0,
        post_run_hold=0,
        timeout_seconds=5,
    )

    assert result == {"success": True, "downloaded": 1}


def test_submit_broker_job_broker_dies(tmp_path: Path, monkeypatch):
    queue_dir = tmp_path / "queue"
    state = BrokerState(
        publisher="elsevier",
        profile_dir=str(tmp_path / "profile"),
        pid=1234,
        queue_dir=str(queue_dir),
        started_at="2026-10-18T10:00:00",
        ttl_seconds=3600,
    )
    write_broker_state(state)

    # Broker stops while waiting for results
    monkeypatch.setattr("litloop.session_broker.broker_is_running", lambda pub: False)
    monkeypatch.setattr("time.sleep", lambda duration: None)

    with pytest.raises(RuntimeError, match="stopped before job completed"):
        submit_broker_job(
            publisher="elsevier",
            records=[],
            output_dir=str(tmp_path / "out"),
            institution="Tsinghua",
            login_timeout=10,
            pdf_timeout=10,
            post_login_hold=0,
            post_run_hold=0,
            timeout_seconds=5,
        )


def test_submit_broker_job_timeout(tmp_path: Path, monkeypatch):
    queue_dir = tmp_path / "queue"
    state = BrokerState(
        publisher="elsevier",
        profile_dir=str(tmp_path / "profile"),
        pid=1234,
        queue_dir=str(queue_dir),
        started_at="2026-10-18T10:00:00",
        ttl_seconds=3600,
    )
    write_broker_state(state)
    monkeypatch.setattr("litloop.session_broker.broker_is_running", lambda pub: True)

    # Simulate timeout by jumping clock ahead
    current_time = 1000.0

    def fake_time():
        nonlocal current_time
        return current_time

    def fake_sleep(duration):
        nonlocal current_time
        current_time += 10.0

    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("time.sleep", fake_sleep)

    with pytest.raises(TimeoutError, match="timed out"):
        submit_broker_job(
            publisher="elsevier",
            records=[],
            output_dir=str(tmp_path / "out"),
            institution="Tsinghua",
            login_timeout=10,
            pdf_timeout=10,
            post_login_hold=0,
            post_run_hold=0,
            timeout_seconds=2,
        )
