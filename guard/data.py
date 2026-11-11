"""Load the frozen dataset. Refuses to load it if the file no longer matches data/FROZEN.json."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "data" / "cases.jsonl"
FROZEN = ROOT / "data" / "FROZEN.json"


class DatasetChanged(RuntimeError):
    pass


def load_cases(split: str | None = None, verify: bool = True) -> list[dict]:
    raw = CASES.read_bytes()
    if verify:
        expected = json.loads(FROZEN.read_text())["sha256"]
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise DatasetChanged(f"data/cases.jsonl sha256 {actual[:12]}... != frozen {expected[:12]}...")
    cases = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    return [c for c in cases if split is None or c["split"] == split]
