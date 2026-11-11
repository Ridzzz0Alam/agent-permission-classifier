"""Two-stage classification pipeline and resumable runner.

Per case:
  1. Tier router. If Tier 1/2 allows the action: decision "allow", no LLM call.
  2. Stage 1: single-word yes/no, temperature 0, fixed seed, num_predict 3. Unparseable -> BLOCK.
  3. If stage 1 blocks: stage 2, same prompt with the stage-2 instruction, num_predict 400.
     Unparseable -> BLOCK.
Both pipelines come from the same run: decision_stage1 (stage 1 only) and decision_full (1 + 2).

SAFETY: this module only sends TEXT to a model and parses the reply. It never executes any command.

Usage:
  .venv/bin/python -m guard.classify --model local --split dev --variants V2 --limit 10
  .venv/bin/python -m guard.classify --model cloud --split dev --variants V1,V2,V3
Runs are resumable: finished (case, model, variant, settings, prompt) records are skipped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from guard.budget import Budget, BudgetExceeded
from guard.clients import BaseClient, ClientError, make_client
from guard.data import load_cases
from guard.render import VARIANT_ALIASES, build_messages, prompt_fingerprint
from guard.tiers import load_allow_rules, route

ROOT = Path(__file__).resolve().parent.parent
DECISIONS = ROOT / "results" / "decisions.jsonl"
STAGE1_MAX_TOKENS = 3
STAGE2_MAX_TOKENS = 800  # raised from 400 after M6 (user-approved): DeepSeek was cut off before its DECISION line
EXIT_BUDGET = 3


# ---------------------------------------------------------------- parsing (fail closed)

def parse_stage1(text: str) -> str | None:
    """'yes' -> block, 'no' -> allow, anything else -> None (caller treats as BLOCK)."""
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return None
    return {"yes": "block", "no": "allow"}.get(words[0])


_DECISION = re.compile(r"DECISION\**\s*[:\-][\s*]*(BLOCK|ALLOW)\b", re.I)  # tolerates **markdown**


def parse_stage2(text: str) -> str | None:
    """Last 'DECISION: BLOCK|ALLOW' in the text; None if there is none."""
    found = _DECISION.findall(text)
    return found[-1].lower() if found else None


# ---------------------------------------------------------------- one case

def _stage_record(res, decision):
    return {"decision": decision or "block", "parse_failure": decision is None, "raw": res.content,
            "thinking_chars": len(res.thinking), "prompt_tokens": res.prompt_tokens,
            "cached_tokens": res.cached_tokens, "output_tokens": res.output_tokens,
            "latency_s": round(res.latency_s, 3), "cost_usd": res.cost_usd, "from_cache": res.from_cache}


def classify_case(case: dict, client: BaseClient | None, variant: str, rules, *,
                  temperature: float = 0.0, seed: int = 42) -> dict:
    variant = VARIANT_ALIASES.get(variant, variant)
    tier = route(case["action"], case["project_dir"], rules, case["prior_tool_calls"])
    rec = {"case_id": case["id"], "split": case["split"], "label": case["label"], "category": case["category"],
           "model": client.model if client else None, "variant": variant, "temperature": temperature, "seed": seed,
           "prompt_fp": prompt_fingerprint()[:16], "s2_max": STAGE2_MAX_TOKENS,
           "tier": tier.tier, "tier_reason": tier.reason,
           "stage1": None, "stage2": None}
    if tier.allowed:
        rec.update(decision_stage1="allow", decision_full="allow", resolved_by="tier")
    else:
        r1 = client.chat(build_messages(case, variant, 1), num_predict=STAGE1_MAX_TOKENS,
                         temperature=temperature, seed=seed)
        rec["stage1"] = _stage_record(r1, parse_stage1(r1.content))
        d1 = rec["stage1"]["decision"]
        if d1 == "allow":
            rec.update(decision_stage1="allow", decision_full="allow", resolved_by="stage1")
        else:
            r2 = client.chat(build_messages(case, variant, 2), num_predict=STAGE2_MAX_TOKENS,
                             temperature=temperature, seed=seed)
            rec["stage2"] = _stage_record(r2, parse_stage2(r2.content))
            rec.update(decision_stage1="block", decision_full=rec["stage2"]["decision"], resolved_by="stage2")
    rec["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return rec


# ---------------------------------------------------------------- runner

def run_key(rec_or_case: dict, model: str, variant: str, temperature: float, seed: int, fp: str,
            s2_max: int = STAGE2_MAX_TOKENS) -> tuple:
    cid = rec_or_case.get("case_id", rec_or_case.get("id"))
    return (cid, model, variant, float(temperature), int(seed), fp, int(s2_max))


def load_done(path: Path) -> set[tuple]:
    done = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(run_key(r, r["model"], r["variant"], r["temperature"], r["seed"], r["prompt_fp"],
                             r.get("s2_max", 400)))  # records before M6 used 400
    return done


def _cost(rec: dict) -> float:
    return sum((rec[s] or {}).get("cost_usd", 0.0) for s in ("stage1", "stage2"))


def run(cases: list[dict], client: BaseClient, variants: list[str], *, temperature=0.0, seed=42,
        out: Path = DECISIONS, log=print) -> int:
    rules = load_allow_rules(ROOT / "config" / "allow_rules.yaml").kept
    fp = prompt_fingerprint()[:16]
    variants = [VARIANT_ALIASES.get(v, v) for v in variants]
    done = load_done(out)
    jobs = [(c, v) for v in variants for c in cases
            if run_key(c, client.model, v, temperature, seed, fp) not in done]
    skipped = len(cases) * len(variants) - len(jobs)
    log(f"{client.model}: {len(jobs)} decisions to make, {skipped} already done (resume). prompt_fp={fp}")
    write_lock = threading.Lock()
    stats = {"n": 0, "cost": 0.0, "cloud_decisions": 0, "t0": time.monotonic()}
    budget: Budget | None = getattr(client, "budget", None)

    def work(case, variant):
        rec = classify_case(case, client, variant, rules, temperature=temperature, seed=seed)
        with write_lock:
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["n"] += 1
            c = _cost(rec)
            stats["cost"] += c
            stats["cloud_decisions"] += 1 if c > 0 else 0
            s1 = rec["stage1"]["raw"].strip()[:12] if rec["stage1"] else "-"
            s2 = rec["decision_full"] if rec["stage2"] else "-"
            lat = sum((rec[s] or {}).get("latency_s", 0) for s in ("stage1", "stage2"))
            pf = " PARSE-FAIL" if any((rec[s] or {}).get("parse_failure") for s in ("stage1", "stage2")) else ""
            ok = "ok " if rec["decision_full"] == rec["label"] else "ERR"
            spend = f"  spent ${budget.spent:.4f}" if budget else ""
            log(f"[{stats['n']}/{len(jobs)}] {ok} {rec['case_id']} {variant:16s} label={rec['label']:5s} "
                f"tier={rec['tier']} s1={s1!r:14s} full={rec['decision_full']:5s} (s2={s2}) {lat:6.1f}s{pf}{spend}")
        return rec

    paused: BudgetExceeded | None = None
    client_error: ClientError | None = None
    with ThreadPoolExecutor(max_workers=client.max_concurrency) as pool:
        pending, queue = set(), list(jobs)
        while queue or pending:
            while queue and len(pending) < client.max_concurrency:
                pending.add(pool.submit(work, *queue.pop(0)))
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in finished:
                exc = f.exception()
                if isinstance(exc, BudgetExceeded):
                    paused = paused or exc
                elif isinstance(exc, ClientError):
                    client_error = client_error or exc
                elif exc is not None:
                    raise exc
            if paused is not None or client_error is not None:
                queue.clear()  # submit nothing more; let in-flight calls finish

    elapsed = time.monotonic() - stats["t0"]
    log(f"done {stats['n']} decisions in {elapsed / 60:.1f} min; cost this run ${stats['cost']:.4f}"
        + (f"; total spent ${budget.spent:.4f} of ${budget.cap:.2f}" if budget else ""))
    if paused is not None:
        remaining = len(jobs) - stats["n"]
        per = stats["cost"] / stats["cloud_decisions"] if stats["cloud_decisions"] else paused.needed
        log("\n*** PAUSED: BUDGET CAP REACHED ***")
        log(f"spent ${paused.spent:.4f} of the ${paused.cap:.2f} cap; {remaining} decisions not made "
            f"(of {len(jobs)} in this run)")
        log(f"projected cost to finish: ~${per * remaining:.2f} (at ${per:.4f} per decision so far)")
        log("Nothing more will be sent. Ask the user whether to continue; resuming needs an explicit --cap.")
        return EXIT_BUDGET
    if client_error is not None:
        log(f"\n*** STOPPED on a client error: {client_error} ***\nRerun the same command to resume.")
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the classifier over dataset cases (resumable).")
    ap.add_argument("--model", choices=["local", "cloud"], required=True)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--variants", default="V2", help="comma list of V1,V2,V3")
    ap.add_argument("--ids", help="comma list of case ids (default: all in the split)")
    ap.add_argument("--limit", type=int, help="first N cases of the split (stratified order is the file order)")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cap", type=float, default=None, help="cloud budget cap in USD (default 5.00)")
    ap.add_argument("--out", type=Path, default=DECISIONS)
    a = ap.parse_args(argv)

    if a.split == "test" and not (ROOT / "config" / "PROMPT_FROZEN.json").exists():
        sys.exit("Refusing to run on the test split: the prompt is not frozen yet (milestone 7).")
    if a.split == "test":
        frozen = json.loads((ROOT / "config" / "PROMPT_FROZEN.json").read_text())["prompt_fp"]
        if frozen != prompt_fingerprint()[:16]:
            sys.exit(f"Refusing: prompt fingerprint {prompt_fingerprint()[:16]} != frozen {frozen}.")

    cases = load_cases(split=a.split)
    if a.ids:
        want = set(a.ids.split(","))
        cases = [c for c in cases if c["id"] in want]
    if a.limit:
        cases = cases[:a.limit]
    budget = Budget(cap_usd=a.cap) if a.cap is not None else Budget()
    client = make_client(a.model, budget=budget)
    return run(cases, client, a.variants.split(","), temperature=a.temperature, seed=a.seed, out=a.out)


if __name__ == "__main__":
    import logging
    logging.disable(logging.WARNING)
    sys.exit(main())
