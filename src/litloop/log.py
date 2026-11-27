"""Logging setup for LitLoop. Routes to stderr to avoid corrupting MCP stdio."""

from __future__ import annotations

import collections
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys
import threading

_logger: logging.Logger | None = None
_file_handler: RotatingFileHandler | None = None
_file_path: Path | None = None
_file_lock = threading.Lock()

# The last few hundred lines, kept in memory so the diagnostics bundle can
# include what happened without the user having to redirect stderr.
_recent: collections.deque[str] = collections.deque(maxlen=1000)

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"((?:api_key|token|secret|password)\s*[=:]\s*)([^\s,;\"']{4,})", re.I), r"\1***"),
    (re.compile(r"(X-LitLoop-Token\s*:\s*)([^\s,;\"']+)", re.I), r"\1***"),
)


def _redact_line(text: str) -> str:
    out = str(text)
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            redacted = _redact_line(rendered)
            if redacted != rendered:
                record.msg = redacted
                record.args = ()
        except (TypeError, ValueError):
            return True
        return True


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _recent.append(_redact_line(self.format(record)))
        except Exception:  # a broken formatter must not break logging
            pass


def recent(limit: int = 200) -> list[str]:
    """The most recent log lines, oldest first."""
    lines = list(_recent)
    return lines[-limit:] if limit > 0 else []


def log_dir() -> Path:
    from . import config
    return Path(config.DATA_DIR) / "logs"


def log_file_path() -> Path | None:
    return _file_path


def disable_file_logging() -> None:
    """Detach and close the active rotating file handler (useful in tests)."""
    global _file_handler, _file_path
    with _file_lock:
        if _logger is not None and _file_handler is not None:
            _logger.removeHandler(_file_handler)
        if _file_handler is not None:
            try:
                _file_handler.close()
            except OSError:
                _file_handler = None
        _file_handler = None
        _file_path = None


def enable_file_logging(
    target_dir: Path | str | None = None,
    *,
    filename: str = "litloop.log",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> Path | None:
    """Attach a UTF-8 rotating file handler to the `litloop` logger."""
    global _file_handler, _file_path
    logger = get_logger()
    directory = Path(target_dir) if target_dir is not None else log_dir()
    target = (directory / filename).resolve()
    with _file_lock:
        if _file_handler is not None and _file_path == target:
            return _file_path
        if _file_handler is not None:
            logger.removeHandler(_file_handler)
            try:
                _file_handler.close()
            except OSError:
                _file_handler = None
            _file_handler = None
            _file_path = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                target,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            handler.setLevel(logging.INFO)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s",
                    "%Y-%m-%d %H:%M:%S",
                )
            )
            handler.addFilter(_RedactFilter())
            logger.addHandler(handler)
            _file_handler = handler
            _file_path = target
            return _file_path
        except OSError:
            return None


def tail_file(limit: int = 500, path: Path | None = None) -> list[str]:
    """Return the last `limit` lines from the persisted log file, falling back to memory ring."""
    target = path or _file_path or (log_dir() / "litloop.log")
    if limit <= 0:
        return []
    try:
        if target.is_file():
            with target.open("r", encoding="utf-8", errors="replace") as fh:
                lines = [ln.rstrip("\r\n") for ln in fh]
            if lines:
                return [_redact_line(ln) for ln in lines[-limit:]]
    except OSError:
        pass
    return recent(limit)


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("litloop")
        if not _logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
            handler.addFilter(_RedactFilter())
            _logger.addHandler(handler)
            _logger.setLevel(logging.INFO)
        # The entry points also basicConfig() a stderr handler on the root
        # logger; propagating there printed every message twice.
        _logger.propagate = False
        if not any(isinstance(h, _RingHandler) for h in _logger.handlers):
            ring = _RingHandler()
            ring.setLevel(logging.INFO)
            ring.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            ring.addFilter(_RedactFilter())
            _logger.addHandler(ring)
    return _logger
