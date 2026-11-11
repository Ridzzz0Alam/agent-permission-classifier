"""Shared helpers for the article examples.

Every example:
  * takes --seed (default 42) and records it,
  * reads only saved results (results/decisions.jsonl, results/llm_cache.jsonl) unless it says otherwise,
  * prints a short aligned table (that printout goes into ARTICLE.md),
  * writes results/<name>_<UTC timestamp>.csv plus a sibling .meta.json.

No example ever executes a command from the dataset: commands are data, shown as text only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.report import VARIANTS, load_test_decisions  # noqa: E402
from guard.render import prompt_fingerprint  # noqa: E402

RESULTS = ROOT / "results"
MODELS = ["deepseek-v4-pro:0813", "llama3.2:3b"]
NICK = {"deepseek-v4-pro:0813": "DeepSeek", "llama3.2:3b": "llama3.2:3b"}
V = list(VARIANTS.values())                 # ["V1", "V2", "V3"]
VKEY = {v: k for k, v in VARIANTS.items()}  # "V2" -> "user_plus_tools"


def parse_args(description: str, extra=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--seed", type=int, default=42, help="seed for any sampling (default 42)")
    if extra:
        extra(ap)
    return ap.parse_args()


# ---------------------------------------------------------------- frozen run

def frozen_fp() -> str:
    return json.loads((ROOT / "config" / "PROMPT_FROZEN.json").read_text())["prompt_fp"]


def frozen_decisions() -> dict[tuple[str, str], list[dict]]:
    """(model, "V1"|"V2"|"V3") -> the 140 latest test decisions of the frozen run."""
    raw = load_test_decisions(frozen_fp())
    return {(m, VARIANTS[vk]): rs for (m, vk), rs in raw.items()}


def stage(r: dict, s: str) -> dict | None:
    x = r.get(s)
    return x if isinstance(x, dict) else None


# ---------------------------------------------------------------- output

def table(rows: list[list], headers: list[str], align: str | None = None) -> str:
    """Aligned plain-text table. align: one char per column, 'l' or 'r' (default: first l, rest r)."""
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    align = align or "l" + "r" * (len(headers) - 1)
    fmt = lambda r: "  ".join(c.ljust(w[i]) if align[i] == "l" else c.rjust(w[i]) for i, c in enumerate(r))
    out = [fmt(cells[0]), "  ".join("-" * x for x in w)] + [fmt(r) for r in cells[1:]]
    return "\n".join(line.rstrip() for line in out)


def _git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", ".", ":!results", ":!figures",
                                ":!diagrams"], cwd=ROOT).returncode != 0
        return sha + ("-dirty" if dirty else "")
    except Exception:  # not a git checkout
        return "unknown"


def _machine() -> dict:
    cpu = platform.processor() or platform.machine()
    ram = None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                ram = f"{int(line.split()[1]) / 1024 / 1024:.1f} GiB"
    except OSError:
        pass
    osname = platform.platform()
    try:
        rel = dict(l.split("=", 1) for l in Path("/etc/os-release").read_text().splitlines() if "=" in l)
        osname = f"{rel.get('PRETTY_NAME', '').strip(chr(34))}, {platform.system()} {platform.release()}"
    except OSError:
        pass
    return {"python": platform.python_version(), "os": osname, "cpu": f"{cpu} ({os.cpu_count()} threads)",
            "ram": ram}


def write_results(name: str, rows: list[dict], args: argparse.Namespace, extra: dict | None = None) -> Path:
    """results/<name>_<UTC timestamp>.csv + .meta.json. Returns the CSV path."""
    RESULTS.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS / f"{name}_{ts}.csv"
    cols: list[str] = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with path.open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        wr.writerows(rows)
    meta = {"name": name, "utc": ts, "command": " ".join([Path(sys.executable).name] + sys.argv),
            "seed": args.seed, "git_commit": _git_commit(),
            "prompt_fp": prompt_fingerprint()[:16], "prompt_fp_frozen": frozen_fp(),
            "dataset_sha256": hashlib.sha256((ROOT / "data" / "cases.jsonl").read_bytes()).hexdigest(),
            "rows": len(rows), **_machine(), **(extra or {})}
    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return path


def done(path: Path) -> None:
    print(f"\nwrote {path.relative_to(ROOT)} (+ .meta.json)")
