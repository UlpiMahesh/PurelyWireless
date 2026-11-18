"""Runner <-> backend protocol (JSON lines on the runner's stdout).

Event types (runner -> backend):
  {"type":"hello",    "pid":123}
  {"type":"log",      "level":"info|warning|error|debug", "msg":"...", "ts":1700000000.1}
  {"type":"stage",    "name":"Logging in"}
  {"type":"progress", "done":3, "total":19}          # only when a REAL total exists
  {"type":"file",     "path":"...", "name":"Allocation", "markets":[...], "date_from":"YYYY-MM-DD", "date_to":"YYYY-MM-DD"}
  {"type":"done",     "summary":{...}}
  {"type":"error",    "message":"...", "traceback":"..."}

This module has no heavy imports: it is used inside the runner subprocess.
"""
from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
from typing import Any, Callable, TextIO

MAX_LINE = 8000

_LEVEL_TOKEN = re.compile(r"(?:\||\[)\s*(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s*(?:\||\])")


def classify_level(text: str, default: str = "info") -> str:
    """Best-effort log level for a captured console line."""
    m = _LEVEL_TOKEN.search(text)
    if m:
        tok = m.group(1).upper()
        return {"WARN": "warning", "WARNING": "warning", "CRITICAL": "error"}.get(tok, tok.lower())
    if "❌" in text or "🚫" in text or text.startswith("Traceback (most recent call last)"):
        return "error"
    if "⚠" in text:
        return "warning"
    return default


class ProtocolWriter:
    """Writes one JSON object per line to the ORIGINAL stdout (thread-safe)."""

    def __init__(self, stream: TextIO):
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, event: dict[str, Any]) -> None:
        # ensure_ascii => the stream never needs a non-ASCII codec (Windows cp1252 safe)
        line = json.dumps(event, ensure_ascii=True, separators=(",", ":"), default=str)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()

    def log(self, msg: str, level: str = "info") -> None:
        if not msg.strip():
            return
        self.send({"type": "log", "level": level, "msg": msg[:MAX_LINE], "ts": time.time()})


class StreamCapture(io.TextIOBase):
    """Replacement for sys.stdout / sys.stderr: each complete line becomes a log event."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, emit_line: Callable[[str], None]):
        self._emit = emit_line
        self._buf = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, s: Any) -> int:
        if isinstance(s, bytes):
            s = s.decode("utf-8", "replace")
        with self._lock:
            self._buf += s
            lines = []
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                lines.append(line.rstrip("\r"))
        for line in lines:
            if line.strip():
                self._emit(line)
        return len(s)

    def flush(self) -> None:  # partial lines stay buffered until a newline / drain()
        pass

    def drain(self) -> None:
        with self._lock:
            rest, self._buf = self._buf, ""
        if rest.strip():
            self._emit(rest.rstrip("\r"))
