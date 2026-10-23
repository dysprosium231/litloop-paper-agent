"""Task progress reporting via a well-known JSON file.

Writers (batch / lanes / fetch) report per-paper progress here; the desktop
app reads it: its own download jobs, and downloads the agent (MCP) or the
command line run in other processes (``running_elsewhere``). The floating
progress bar that also watched it was removed on 2026-10-19 (the user's call).

File: <data_dir>/progress/current.json — atomically replaced (tmp + rename)
on every update, so readers never see a torn write.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}
_STARTED_MONO: float = 0.0

STALE_SECONDS = 120  # reader shows "idle" when no update for this long

# In-process listeners told about every finished item: (ok, current). The
# desktop app's download jobs use this to mark papers one by one.
_LISTENERS: list[Any] = []


def add_listener(fn: Any) -> None:
    _LISTENERS.append(fn)


def remove_listener(fn: Any) -> None:
    try:
        _LISTENERS.remove(fn)
    except ValueError:
        pass


# Listeners told where one paper is now: (identifier, text). The app shows it
# under the paper ("已在 Sci-Hub 找到，正在下载"); nothing is written to disk here.
_STAGE_LISTENERS: list[Any] = []


def add_stage_listener(fn: Any) -> None:
    _STAGE_LISTENERS.append(fn)


def remove_stage_listener(fn: Any) -> None:
    try:
        _STAGE_LISTENERS.remove(fn)
    except ValueError:
        pass


# Listeners told a paper's final result: (identifier, result dict). Sent when
# the engine knows nothing more will be tried for it (a short list's single
# downloads), so the app shows it at once instead of when the job ends.
_RESULT_LISTENERS: list[Any] = []


def add_result_listener(fn: Any) -> None:
    _RESULT_LISTENERS.append(fn)


def remove_result_listener(fn: Any) -> None:
    try:
        _RESULT_LISTENERS.remove(fn)
    except ValueError:
        pass


def item_result(identifier: str, result: dict) -> None:
    """A paper is finished for good (downloaded or given up); never raises."""
    for fn in list(_RESULT_LISTENERS):
        try:
            fn(identifier, result)
        except Exception:
            pass


def item_stage(identifier: str, text: str) -> None:
    """Say what is happening to one paper; never raises."""
    for fn in list(_STAGE_LISTENERS):
        try:
            fn(identifier, text)
        except Exception:
            pass


def _progress_dir() -> Path:
    data_dir = Path(os.environ.get("LITLOOP_DATA_DIR", str(Path.home() / ".litloop")))
    return data_dir / "progress"


def progress_path() -> Path:
    return _progress_dir() / "current.json"


def _atomic_write(payload: dict[str, Any]) -> None:
    path = progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _pid_alive(pid: int) -> bool:
    """Windows-safe liveness probe. NEVER use os.kill(pid, 0) on Windows —
    any signal value there calls TerminateProcess and would murder the bar."""
    if pid <= 0:
        return False
    try:
        if os.name != "nt":
            os.kill(pid, 0)
            return True
        import ctypes

        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(int(handle))
        return True
    except Exception:
        return False


def start_task(task: str, total: int, **extra: Any) -> None:
    """Begin (or restart) the reported task with a known total."""
    global _STATE, _STARTED_MONO
    with _LOCK:
        _STARTED_MONO = time.monotonic()
        _STATE = {
            "task": task,
            "total": int(total),
            "done": 0,
            "success": 0,
            "failed": 0,
            "current": "",
            "phase": "",
            "status": "running",
            "attention": {},
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "pid": os.getpid(),  # readers can tell a live task from a crashed one
            **extra,
        }
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _atomic_write(dict(_STATE))
        except Exception:
            pass


def update(**kw: Any) -> None:
    """Merge fields into the current report (done/success/failed/current/...)."""
    with _LOCK:
        if not _STATE:
            return
        _STATE.update(kw)
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _atomic_write(dict(_STATE))
        except Exception:
            pass


def advance(ok: bool, current: str = "", phase: str = "") -> None:
    """Record one finished item (the hot path for per-paper lanes)."""
    for fn in list(_LISTENERS):
        try:
            fn(ok, current)
        except Exception:
            pass
    with _LOCK:
        if not _STATE:
            return
        _STATE["done"] = int(_STATE.get("done", 0)) + 1
        _STATE["success"] = int(_STATE.get("success", 0)) + (1 if ok else 0)
        _STATE["failed"] = int(_STATE.get("failed", 0)) + (0 if ok else 1)
        if current:
            _STATE["current"] = current
        if phase:
            _STATE["phase"] = phase
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _atomic_write(dict(_STATE))
        except Exception:
            pass


def set_attention(key: str, message: str, *, current: str = "", phase: str = "") -> None:
    """Show a manual-action reminder without stopping other workers."""
    with _LOCK:
        if not _STATE:
            return
        attention = _STATE.setdefault("attention", {})
        if not isinstance(attention, dict):
            attention = {}
            _STATE["attention"] = attention
        before = attention.get(str(key)) or {}
        attention[str(key)] = {
            "message": str(message),
            "current": str(current or ""),
            "phase": str(phase or ""),
            # When the notice first appeared: a waiting source repeats it on
            # every poll, which must not move this.
            "since": before.get("since") or datetime.now().isoformat(timespec="seconds"),
        }
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _atomic_write(dict(_STATE))
        except Exception:
            pass


def set_output_dir(path) -> None:
    """Record the task's output folder (the report carries it for readers)."""
    with _LOCK:
        if not _STATE:
            return
        _STATE["output_dir"] = str(path)
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _atomic_write(dict(_STATE))
        except Exception:
            pass


def clear_attention(key: str) -> None:
    """Remove one manual-action reminder after its browser page clears."""
    with _LOCK:
        if not _STATE:
            return
        attention = _STATE.get("attention")
        if not isinstance(attention, dict):
            return
        attention.pop(str(key), None)
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _atomic_write(dict(_STATE))
        except Exception:
            pass


def finish() -> None:
    """Mark the task done (the bar keeps the final state briefly, then idles)."""
    with _LOCK:
        if not _STATE:
            return
        _STATE["status"] = "done"
        _STATE["attention"] = {}
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _STATE["finished_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _atomic_write(dict(_STATE))
        except Exception:
            pass


def elapsed_sec() -> float:
    return max(0.0, time.monotonic() - _STARTED_MONO)


def running_elsewhere() -> dict[str, Any] | None:
    """A download another process is running now (the agent through MCP, the
    command line), for the desktop app to show; None when there is none, when
    the report is this process's own, or when its writer has died."""
    state = read_state()
    if not state or state.get("status") != "running":
        return None
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid == os.getpid() or (pid and not _pid_alive(pid)):
        return None
    return state


def latest_notice() -> dict[str, Any] | None:
    """The latest download report, running or finished, whichever process wrote
    it: what the desktop app's system notifications need (a new request for the
    user's action, a finished list). None when there is none, or when a running
    report's writer has died."""
    state = read_state()
    if not state:
        return None
    if state.get("status") == "running":
        try:
            pid = int(state.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and pid != os.getpid() and not _pid_alive(pid):
            return None
    keys = ("task", "status", "total", "done", "success", "failed", "started_at", "finished_at", "attention")
    return {k: state.get(k) for k in keys}


def read_state() -> dict[str, Any] | None:
    """Reader side: latest report, or None when absent/stale."""
    try:
        raw = progress_path().read_text(encoding="utf-8")
        state = json.loads(raw)
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    if state.get("status") == "done":
        return state  # finished states stay visible a while (reader decides)
    try:
        ts = datetime.fromisoformat(str(state.get("updated_at")))
        age = (datetime.now() - ts).total_seconds()
        if age > STALE_SECONDS:
            return None
    except Exception:
        pass
    return state
