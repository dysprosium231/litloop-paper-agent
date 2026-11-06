"""Logging setup for LitLoop. Routes to stderr to avoid corrupting MCP stdio."""

from __future__ import annotations

import collections
import logging
import sys

_logger: logging.Logger | None = None

# The last few hundred lines, kept in memory so the diagnostics bundle can
# include what happened without the user having to redirect stderr.
_recent: collections.deque[str] = collections.deque(maxlen=400)


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _recent.append(self.format(record))
        except Exception:  # a broken formatter must not break logging
            pass


def recent(limit: int = 200) -> list[str]:
    """The most recent log lines, oldest first."""
    lines = list(_recent)
    return lines[-limit:] if limit > 0 else []


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("litloop")
        if not _logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
            _logger.addHandler(handler)
            _logger.setLevel(logging.INFO)
        # The entry points also basicConfig() a stderr handler on the root
        # logger; propagating there printed every message twice.
        _logger.propagate = False
        if not any(isinstance(h, _RingHandler) for h in _logger.handlers):
            ring = _RingHandler()
            ring.setLevel(logging.INFO)
            ring.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            _logger.addHandler(ring)
    return _logger
