"""Files shared by the agent (through MCP) and the desktop app.

Both sides run in different processes and read and write the same files
under ``<litloop home>/screening``:

* ``<session>.json``          screening record (ratings, reasons, the user's picks)
* ``<session>.summary.json``  deep-search overview for the app (small; the pool is not)
* ``<session>.progress.json`` what a running deep-search round is doing right now
* ``<session>.pool.json``     deep-search candidate pool (agent only; can be 100+ MB)

Rules: a writer holds the file's lock (``filelock``, works across processes on
Windows, macOS and Linux) for the whole read-modify-write; every write goes
to a temporary file first and replaces the old one in one step, so a reader
never sees half a file. Readers need no lock. Each shared file carries
``schema``; a reader that finds a newer schema than it knows asks for an
update instead of misreading it (the app ships its own copy of this code).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock

SCHEMA = 1
LOCK_TIMEOUT = 60  # seconds; a deep-search round holds a session lock only while saving


class SchemaTooNew(RuntimeError):
    """The file was written by a newer LitLoop; this copy must be updated to read it."""


def lock(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock", timeout=LOCK_TIMEOUT)


def _replace(tmp: Path, path: Path) -> None:
    # On Windows the replace fails while another process has the target open
    # for reading; readers hold it only for a moment, so try again briefly.
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace(tmp, path)


def write_json(path: Path, data: Any, *, indent: int | None = None) -> None:
    if isinstance(data, dict):
        data.setdefault("schema", SCHEMA)
    write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def read_json(path: Path) -> Any:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and (data.get("schema") or 0) > SCHEMA:
        raise SchemaTooNew(f"{Path(path).name} was written by a newer LitLoop (schema {data['schema']}, "
                           f"this copy knows {SCHEMA}); update LitLoop")
    return data


@contextmanager
def update(path: Path, *, indent: int | None = None) -> Iterator[Any]:
    """Read, let the caller change, and write back ``path`` under its lock."""
    with lock(path):
        data = read_json(path)
        yield data
        write_json(path, data, indent=indent)


# --------------------------------------------------------------------------- deep-search progress

def _screening_dir() -> Path:
    from .screening import screening_dir
    return screening_dir()


def progress_path(session: str) -> Path:
    return _screening_dir() / f"{session}.progress.json"


def summary_path(session: str) -> Path:
    return _screening_dir() / f"{session}.summary.json"


class Progress:
    """What a deep-search round is doing, for the app to show while it runs.

    ``phase(...)`` starts a step (expand, abstracts, rank), ``step(done)``
    advances it, ``finish()`` marks the round idle. Writes are cheap and at
    most a few per second, so the app can poll the file.
    """

    MIN_INTERVAL = 0.5

    def __init__(self, session: str, event: str):
        self.path = progress_path(session)
        self.data = {"session": session, "event": event, "state": "running",
                     "started": time.time(), "phase": None, "phases": []}
        self._last = 0.0

    def _write(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last < self.MIN_INTERVAL:
            return
        self._last = now
        self.data["updated"] = now
        try:
            write_json(self.path, self.data)
        except OSError:
            pass  # progress is a convenience; never fail a search over it

    def phase(self, name: str, total: int | None = None) -> None:
        if self.data["phase"]:
            self.data["phases"].append({"name": self.data["phase"]["name"],
                                        "seconds": round(time.time() - self.data["phase"]["started"], 1)})
        self.data["phase"] = {"name": name, "done": 0, "total": total, "started": time.time()}
        self._write(force=True)

    def step(self, done: int, total: int | None = None) -> None:
        if self.data["phase"]:
            self.data["phase"]["done"] = done
            if total is not None:
                self.data["phase"]["total"] = total
            self._write()

    def finish(self, state: str = "idle") -> None:
        self.phase("done")
        self.data["phase"] = None
        self.data["state"] = state
        self.data["seconds"] = round(time.time() - self.data["started"], 1)
        self._write(force=True)


def read_progress(session: str) -> dict[str, Any] | None:
    try:
        return read_json(progress_path(session))
    except (OSError, ValueError):
        return None
