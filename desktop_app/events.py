from __future__ import annotations

import logging
import json
import os
import re
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("sap_proxy")


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|password|authtoken|authorization)(\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~-]+)"),
)


def redact(message: str) -> str:
    value = message
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 3:
            value = pattern.sub(r"\1\2[redacted]", value)
        else:
            value = pattern.sub(r"\1[redacted]", value)
    return value


class EventLog:
    def __init__(self, max_entries: int = 600, log_path: Path | None = None):
        self._lock = threading.Lock()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._sequence = 0
        self._log_path = log_path
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            if self._log_path.exists() and self._log_path.stat().st_size > 5 * 1024 * 1024:
                self._log_path.replace(self._log_path.with_suffix(".log.1"))
            self._log_path.touch(exist_ok=True)
            os.chmod(self._log_path, 0o600)

    def add(self, level: str, message: str) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            entry = {
                "id": self._sequence,
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "level": level.upper(),
                "message": redact(str(message)),
            }
            self._entries.append(entry)
            if self._log_path:
                try:
                    with self._log_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
                except OSError:
                    pass
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.log(log_level, "%s", entry["message"])
        return entry

    def debug(self, message: str) -> None:
        self.add("DEBUG", message)

    def info(self, message: str) -> None:
        self.add("INFO", message)

    def warning(self, message: str) -> None:
        self.add("WARNING", message)

    def error(self, message: str) -> None:
        self.add("ERROR", message)

    def since(self, sequence: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._entries if entry["id"] > sequence]

    @property
    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence
