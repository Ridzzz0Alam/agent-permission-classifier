"""Replay validation: the saved test-split results are exactly what this code produces.

There is no hidden upstream implementation to model (Anthropic's classifier is not open source),
so instead we prove that every number in ARTICLE.md can be re-derived from the saved model replies:

  1. every stored raw reply re-parses to the stored decision (stage 1, stage 2, full pipeline),
  2. every case re-routes to the stored tier and reason,
  3. the prompt and dataset are byte-identical to the frozen versions,
  4. the fresh calls' cost adds up to the budget ledger's spend for the test run.

No model is called and no dataset command is executed.

    .venv/bin/python -m pytest tests/test_replay.py -v
"""
import hashlib
import json
from pathlib import Path

import pytest

from analysis.report import frozen_fp, load_test_decisions
from guard.classify import parse_stage1, parse_stage2
from guard.data import load_cases
from guard.render import prompt_fingerprint
from guard.tiers import load_allow_rules, route

ROOT = Path(__file__).resolve().parent.parent
FP = frozen_fp()
D = load_test_decisions(FP)
RECORDS = [r for rs in D.values() for r in rs]
CASES = {c["id"]: c for c in load_cases(split="test")}


def test_frozen_inputs_unchanged():
    frozen = json.loads((ROOT / "config" / "PROMPT_FROZEN.json").read_text())
    assert prompt_fingerprint() == frozen["prompt_fp_full"]
    for f, sha in frozen["files_sha256"].items():
        assert hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == sha, f
    data_sha = json.loads((ROOT / "data" / "FROZEN.json").read_text())["sha256"]
    assert hashlib.sha256((ROOT / "data" / "cases.jsonl").read_bytes()).hexdigest() == data_sha


def test_complete_run():
    # 2 models x 3 variants x 140 test cases
    assert len(D) == 6 and all(len(rs) == len(CASES) == 140 for rs in D.values())


def test_replies_reparse_to_stored_decisions():
    mismatches = []
    for r in RECORDS:
        if r["tier"] != 3:
            ok = r["decision_stage1"] == r["decision_full"] == "allow" and r["stage1"] is None
        else:
            d1 = parse_stage1(r["stage1"]["raw"]) or "block"  # fail closed
            ok = d1 == r["stage1"]["decision"] == r["decision_stage1"]
            if d1 == "allow":
                ok &= r["decision_full"] == "allow" and r["stage2"] is None
            else:
                d2 = parse_stage2(r["stage2"]["raw"])
                ok &= (d2 or "block") == r["stage2"]["decision"] == r["decision_full"]
                ok &= r["stage2"]["parse_failure"] == (d2 is None)
        if not ok:
            mismatches.append((r["model"], r["variant"], r["case_id"]))
    assert not mismatches, mismatches[:5]


def test_cases_reroute_to_stored_tier():
    rules = load_allow_rules(ROOT / "config" / "allow_rules.yaml").kept
    for r in RECORDS:
        c = CASES[r["case_id"]]
        t = route(c["action"], c["project_dir"], rules, c["prior_tool_calls"])
        assert (t.tier, t.reason) == (r["tier"], r["tier_reason"]), r["case_id"]


def test_cost_matches_budget_ledger():
    fresh = sum(r[s]["cost_usd"] for r in RECORDS if r["model"].startswith("deepseek")
                for s in ("stage1", "stage2") if r.get(s) and not r[s]["from_cache"])
    ledger = sum(json.loads(l)["cost_usd"] for l in (ROOT / "results" / "cloud_spend.jsonl").open()
                 if json.loads(l)["ts"] >= "2026-11-08T08:3")
    assert fresh == pytest.approx(ledger, abs=0.005)
    assert fresh == pytest.approx(0.8993, abs=0.0005)
