"""Append-only response cache, so no model call is ever made twice.

The key is a hash of everything that determines the response: model, endpoint kind, full messages,
and sampling options. Any prompt edit therefore produces new keys automatically. The file is JSONL,
git-friendly, and tolerant of a final line truncated by a crash. It never contains the API key.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "results" / "llm_cache.jsonl"


def cache_key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ResponseCache:
    def __init__(self, path: Path = CACHE_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # partial line from an interrupted write
                self._data[rec["key"]] = rec

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, record: dict) -> None:
        rec = {"key": key, **record}
        with self._lock:
            self._data[key] = rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
