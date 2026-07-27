"""Thread-safe in-memory log buffer — captures logger output for the UI Traces tab."""
from __future__ import annotations

import logging
import threading

_lock:   threading.Lock = threading.Lock()
_buffer: list[str]      = []
_MAX    = 500


class BufferHandler(logging.Handler):
    """Appends formatted log records to the shared buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            with _lock:
                _buffer.append(line)
                if len(_buffer) > _MAX:
                    del _buffer[: len(_buffer) - _MAX]
        except Exception:
            pass


def get_lines() -> list[str]:
    with _lock:
        return list(_buffer)


def clear() -> None:
    with _lock:
        _buffer.clear()
