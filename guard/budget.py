"""Hard budget stop for cloud calls.

Every billed call is appended to results/cloud_spend.jsonl, so the total spend persists across runs
and processes. Before each call the client reserves the worst-case cost of that call. If the spend so
far, plus calls already in flight, plus this call could exceed the cap, BudgetExceeded is raised and
the call is never sent. The cap is never raised automatically: only the user decides that.
"""

from __future__ import annotations

import itertools
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "results" / "cloud_spend.jsonl"
DEFAULT_CAP_USD = 5.00

# USD per 1M tokens, PEAK rates (conservative), ollama.com/pricing, checked 2026-11-07.
RATES = {
    "deepseek-v4-pro:0813": {"input": 1.32, "cached_input": 0.044, "output": 3.96},
}


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: float, in_flight: float, needed: float, cap: float):
        self.spent, self.in_flight, self.needed, self.cap = spent, in_flight, needed, cap
        super().__init__(f"budget cap ${cap:.2f} would be exceeded: spent ${spent:.4f} + in flight "
                         f"${in_flight:.4f} + this call (worst case) ${needed:.4f}")


def call_cost(model: str, prompt_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    if model not in RATES:
        raise KeyError(f"no published rates for {model!r}; refusing to call it")
    r = RATES[model]
    cached = min(max(cached_tokens, 0), prompt_tokens)
    return ((prompt_tokens - cached) * r["input"] + cached * r["cached_input"] + output_tokens * r["output"]) / 1e6


class Budget:
    def __init__(self, cap_usd: float = DEFAULT_CAP_USD, ledger: Path = LEDGER):
        self.cap = cap_usd
        self.ledger = ledger
        self._lock = threading.Lock()
        self._reserved: dict[int, float] = {}
        self._ids = itertools.count()
        self._spent = 0.0
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                if line.strip():
                    self._spent += json.loads(line)["cost_usd"]

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent

    @property
    def remaining(self) -> float:
        return self.cap - self.spent

    def reserve(self, model: str, est_prompt_tokens: int, max_output_tokens: int) -> int:
        """Reserve the worst case (all input uncached, full output). Raises BudgetExceeded."""
        worst = call_cost(model, est_prompt_tokens, 0, max_output_tokens)
        with self._lock:
            in_flight = sum(self._reserved.values())
            if self._spent + in_flight + worst > self.cap:
                raise BudgetExceeded(self._spent, in_flight, worst, self.cap)
            rid = next(self._ids)
            self._reserved[rid] = worst
            return rid

    def release(self, rid: int) -> None:
        with self._lock:
            self._reserved.pop(rid, None)

    def record(self, rid: int, model: str, prompt_tokens: int, cached_tokens: int, output_tokens: int,
               source: str = "classify") -> float:
        cost = call_cost(model, prompt_tokens, cached_tokens, output_tokens)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "source": source, "model": model,
                 "prompt_tokens": prompt_tokens, "cached_tokens": cached_tokens,
                 "output_tokens": output_tokens, "cost_usd": cost}
        with self._lock:
            self._reserved.pop(rid, None)
            self._spent += cost
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        return cost
