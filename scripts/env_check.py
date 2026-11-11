"""Milestone 1: environment check.

Makes one tiny call to the local model and one to the cloud model, and prints the
token counts each returns. The prompts are harmless fixed strings; nothing from the
dataset is used, and no command is ever executed.

Usage:
    .venv/bin/python scripts/env_check.py            # both
    .venv/bin/python scripts/env_check.py --local    # local only
    .venv/bin/python scripts/env_check.py --cloud    # cloud only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "results" / "cloud_spend.jsonl"  # cumulative cloud spend; reused by guard/budget.py later
BUDGET_CAP_USD = 5.00

LOCAL_URL = "http://localhost:11434/api/chat"
LOCAL_MODEL = "llama3.2:3b"
CLOUD_URL = "https://ollama.com/api/chat"
CLOUD_MODEL = "deepseek-v4-pro:0813"
# USD per 1M tokens, peak rates (conservative), from ollama.com/pricing on 2026-11-07
RATES = {"input": 1.32, "cached_input": 0.044, "output": 3.96}

PROMPT = "Reply with exactly one word: ready"


def spent_so_far() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(line)["cost_usd"] for line in LEDGER.read_text().splitlines() if line.strip())


def cost_usd(prompt_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    uncached = max(prompt_tokens - cached_tokens, 0)
    return (uncached * RATES["input"] + cached_tokens * RATES["cached_input"] + output_tokens * RATES["output"]) / 1e6


def show(label: str, data: dict, latency: float) -> None:
    msg = data.get("message", {})
    print(f"[{label}] model={data.get('model')}")
    print(f"  reply           : {msg.get('content', '').strip()[:80]!r}")
    if msg.get("thinking"):
        print(f"  thinking (chars): {len(msg['thinking'])}  <-- model emitted thinking despite think=false")
    print(f"  prompt tokens   : {data.get('prompt_eval_count')}")
    print(f"  cached prompt   : {data.get('prompt_eval_cached_count', 'n/a')}")
    print(f"  output tokens   : {data.get('eval_count')}")
    print(f"  latency (s)     : {latency:.2f}")


def check_local() -> bool:
    body = {
        "model": LOCAL_MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "options": {"temperature": 0, "seed": 42, "num_predict": 5, "num_ctx": 4096, "num_thread": 4},
    }
    t0 = time.monotonic()
    try:
        r = requests.post(LOCAL_URL, json=body, timeout=600)
    except requests.ConnectionError:
        print("[local] FAIL: cannot reach Ollama at localhost:11434 (is `ollama serve` running?)")
        return False
    if r.status_code != 200:
        print(f"[local] FAIL: HTTP {r.status_code}: {r.text[:200]}")
        return False
    show("local", r.json(), time.monotonic() - t0)
    return True


def check_cloud() -> bool:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if not key:
        print("[cloud] FAIL: OLLAMA_API_KEY not found in .env or environment")
        return False

    max_out = 200
    worst_case = cost_usd(100, 0, max_out)
    if spent_so_far() + worst_case > BUDGET_CAP_USD:
        print(f"[cloud] STOP: budget cap ${BUDGET_CAP_USD:.2f} would be exceeded (spent ${spent_so_far():.4f}).")
        return False

    body = {
        "model": CLOUD_MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": 42, "num_predict": max_out},
    }
    headers = {"Authorization": f"Bearer {key}"}
    t0 = time.monotonic()
    r = requests.post(CLOUD_URL, json=body, headers=headers, timeout=120)
    latency = time.monotonic() - t0
    if r.status_code != 200:
        # never echo headers; the body from the server does not contain the key
        print(f"[cloud] FAIL: HTTP {r.status_code}: {r.text[:300]}")
        return False

    data = r.json()
    show("cloud", data, latency)
    p, c, o = data.get("prompt_eval_count") or 0, data.get("prompt_eval_cached_count") or 0, data.get("eval_count") or 0
    cost = cost_usd(p, c, o)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(), "source": "env_check", "model": CLOUD_MODEL,
            "prompt_tokens": p, "cached_tokens": c, "output_tokens": o, "cost_usd": cost,
        }) + "\n")
    print(f"  cost (USD)      : {cost:.6f}   cumulative: {spent_so_far():.6f} / {BUDGET_CAP_USD:.2f}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--cloud", action="store_true")
    a = ap.parse_args()
    both = not (a.local or a.cloud)
    ok = True
    if both or a.local:
        ok &= check_local()
    if both or a.cloud:
        ok &= check_cloud()
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
