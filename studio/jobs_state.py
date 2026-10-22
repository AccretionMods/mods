"""Bounded local job history and cancellation-aware resource scheduling."""

import json
import math
import os
import re
import threading
import uuid
from pathlib import Path

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)
}
_SENSITIVE = (
    r"(?:[\w-]*(?:token|password|passwd|secret)|api[_-]?key|authorization|cookie)"
)
_ASSIGNMENT = re.compile(
    rf"""(?i)(["']?\b{_SENSITIVE}["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}}]+)"""
)
_HEADER = re.compile(r"(?im)\b(?:authorization|cookie|set-cookie)\s*:\s*[^\n]*")
_FLAG = re.compile(rf"(?i)(--?{_SENSITIVE})(?:\s+|=)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s]+)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b")
_CLAUDE = re.compile(
    r"(?i)(?:\bclaude(?:\.exe|\.cmd)?\s|--(?:append-system-)?prompt\b)"
)
_STATUSES = {
    "queued",
    "running",
    "stopping",
    "done",
    "failed",
    "stopped",
    "interrupted",
}
_ACTIVE = {"queued", "running", "stopping"}
MAX_LOG_BYTES = 512 * 1024
MAX_LOG_LINES = 2000
MAX_LINE_CHARS = 8192
MAX_RECORD_BYTES = 4 * 1024 * 1024


def _safe_id(identifier):
    if (
        not isinstance(identifier, str)
        or not _ID.fullmatch(identifier)
        or identifier.lower() in _RESERVED
    ):
        raise ValueError("Invalid job id")
    return identifier


def _redact(text):
    text = _HEADER.sub("[redacted header]", text)
    text = _ASSIGNMENT.sub(lambda match: match[1] + "[redacted]", text)
    text = _FLAG.sub(lambda match: match[1] + " [redacted]", text)
    return _JWT.sub("[redacted JWT]", text)


def redact(text: str) -> str:
    """Redact credential patterns in a live output line without storing it."""
    return _redact(text)


def _args(values):
    if not isinstance(values, list):
        return []
    result, hide_next = [], False
    for value in values[:100]:
        if not isinstance(value, str):
            continue
        if hide_next:
            result.append("[redacted]")
            hide_next = False
            continue
        if _CLAUDE.search(value) or value in (
            "-p",
            "--prompt",
            "--append-system-prompt",
        ):
            # A full command or a following prompt must never become resumable history.
            result.append("[omitted command/prompt]")
            hide_next = value in ("-p", "--prompt", "--append-system-prompt")
            continue
        hide_next = bool(re.fullmatch(rf"(?i)--?{_SENSITIVE}", value))
        result.append(_redact(value)[:MAX_LINE_CHARS])
    return result


def _lines(values):
    if not isinstance(values, list):
        return []
    result, size = [], 0
    for value in reversed(values):
        if not isinstance(value, str) or value.lstrip().startswith(">"):
            continue  # Interactive input is represented as '> typed answer' by Job.send.
        if _CLAUDE.search(value):
            value = "[omitted command/prompt]"
        value = _redact(value)[:MAX_LINE_CHARS]
        raw_size = len(value.encode("utf-8"))
        if size + raw_size > MAX_LOG_BYTES or len(result) >= MAX_LOG_LINES:
            break
        result.append(value)
        size += raw_size
    return list(reversed(result))


def _view(view):
    if not isinstance(view, dict):
        raise TypeError("Invalid job record")
    identifier = _safe_id(view.get("id"))
    status = view.get("status")
    if status not in _STATUSES:
        raise ValueError("Invalid job status")
    command = view.get("command")
    if not isinstance(command, str) or (
        command != "--help"
        and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", command)
    ):
        raise ValueError("Invalid job command")
    started = view.get("started")
    if type(started) not in (int, float) or not math.isfinite(started):
        raise ValueError("Invalid job start time")
    lines = _lines(view.get("lines", []))
    code = view.get("code")
    result = {
        "id": identifier,
        "command": command,
        "args": [] if command.lower() == "claude" else _args(view.get("args", [])),
        "status": status,
        "code": code if type(code) is int else None,
        "started": started,
        "interactive": False,
        "lines": lines,
        "next": len(lines),
    }
    for key in ("finished", "ended"):
        value = view.get(key)
        if type(value) in (int, float) and math.isfinite(value):
            result[key] = value
    return result


class History:
    """Persist safe job views only; recovered work is never resumed automatically."""

    def __init__(self, state_root: Path):
        self.root = Path(state_root).resolve()
        self.folder = self.root / "jobs"
        self._lock = threading.Lock()

    def _check_folder(self):
        if not self.folder.resolve().is_relative_to(self.root):
            raise ValueError("Job history escapes local state")

    def save(self, view: dict) -> None:
        clean = _view(view)
        encoded = json.dumps(clean, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("Job record too large")
        with self._lock:
            self._check_folder()
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / (clean["id"] + ".json")
            if path.is_symlink():
                raise ValueError("Job record cannot be a link")
            temporary = self.folder / ("." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(encoded)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def load(self) -> list[dict]:
        result = []
        with self._lock:
            try:
                self._check_folder()
                paths = list(self.folder.glob("*.json"))
            except (OSError, ValueError):
                return []
            for path in paths:
                try:
                    if path.is_symlink() or path.stat().st_size > MAX_RECORD_BYTES:
                        continue
                    with path.open("rb") as stream:
                        raw = stream.read(MAX_RECORD_BYTES + 1)
                    if len(raw) > MAX_RECORD_BYTES:
                        continue
                    clean = _view(json.loads(raw.decode("utf-8")))
                    if clean["id"] != path.stem:
                        continue
                    if clean["status"] in _ACTIVE:
                        clean["status"] = "interrupted"
                    result.append(clean)
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    UnicodeError,
                    OverflowError,
                    RecursionError,
                ):
                    continue
        return sorted(result, key=lambda row: (row["started"], row["id"]))


class Coordinator:
    """FIFO among conflicting resource requests; disjoint requests may run together."""

    def __init__(self):
        self._condition = threading.Condition()
        self._active = set()
        self._pending = []

    @staticmethod
    def _resources(resources):
        if isinstance(resources, str):
            raise TypeError("Resources must be an iterable of names")
        values = frozenset(resources)
        if any(not isinstance(r, str) or not r or len(r) > 200 for r in values):
            raise ValueError("Invalid job resource")
        return values

    def acquire(self, resources, cancelled: threading.Event) -> bool:
        resources = self._resources(resources)
        ticket = (object(), resources)
        with self._condition:
            self._pending.append(ticket)
            try:
                while True:
                    if cancelled.is_set():
                        return False
                    earlier = self._pending[: self._pending.index(ticket)]
                    if not (resources & self._active) and not any(
                        resources & r for _, r in earlier
                    ):
                        self._active.update(resources)
                        return True
                    # threading.Event.set does not notify a Condition; bound cancellation latency.
                    self._condition.wait(timeout=0.05)
            finally:
                self._pending.remove(ticket)
                self._condition.notify_all()

    def release(self, resources) -> None:
        resources = self._resources(resources)
        with self._condition:
            self._active.difference_update(resources)
            self._condition.notify_all()
