"""Pipeline, cache, budget and resume tests. No network: fake clients only."""

import json

import pytest

from guard.budget import Budget, BudgetExceeded, call_cost
from guard.cache import ResponseCache
from guard.classify import EXIT_BUDGET, classify_case, parse_stage1, parse_stage2, run
from guard.clients import BaseClient, ChatResult, CloudClient
from guard.data import load_cases
from guard.tiers import load_allow_rules

CASES = {c["id"]: c for c in load_cases()}
RULES = load_allow_rules("config/allow_rules.yaml").kept
MODEL = "deepseek-v4-pro:0813"


class FakeLocal(BaseClient):
    """Answers stage 1 with s1 and stage 2 with s2; counts real sends (cache misses)."""
    kind = "fake"

    def __init__(self, cache, s1="yes", s2="Reasoning...\nDECISION: ALLOW"):
        super().__init__("fake-model", cache)
        self.s1, self.s2, self.sends = s1, s2, 0

    def payload(self, messages, num_predict, temperature, seed):
        return {"model": self.model, "messages": messages, "n": num_predict, "t": temperature, "s": seed}

    def _send(self, payload, max_output):
        self.sends += 1
        text = self.s1 if payload["n"] == 3 else self.s2
        return ChatResult(text, "", 2000, 0, 5, 0.01, 0.0)


class FakeCloud(CloudClient):
    """A CloudClient whose HTTP layer is fake; budget logic is real."""

    def __init__(self, budget, cache):
        super().__init__(budget=budget, cache=cache, api_key="FAKE-KEY-never-written")
        self.sends = 0

    def _post(self, payload):
        self.sends += 1
        is_s1 = payload["options"]["num_predict"] == 3

        class R:
            def json(_self):
                return {"message": {"content": "yes" if is_s1 else "DECISION: BLOCK"},
                        "prompt_eval_count": 2000, "prompt_eval_cached_count": 1800,
                        "eval_count": 1 if is_s1 else 300}
        return R()


@pytest.fixture
def tmp(tmp_path):
    return {"cache": ResponseCache(tmp_path / "cache.jsonl"), "ledger": tmp_path / "spend.jsonl",
            "out": tmp_path / "decisions.jsonl", "dir": tmp_path}


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("text,want", [
    ("yes", "block"), ("Yes.", "block"), ("YES (block)", "block"), (" no", "allow"), ("No.", "allow"),
    ("**no**", "allow"), ("", None), ("Block", None), ("I think yes", None), ("maybe", None),
])
def test_parse_stage1(text, want):
    assert parse_stage1(text) == want


@pytest.mark.parametrize("text,want", [
    ("...\nDECISION: BLOCK", "block"), ("DECISION: ALLOW", "allow"), ("**DECISION:** **ALLOW**", "allow"),
    ("**DECISION**: BLOCK", "block"), ("DECISION - allow", "allow"),
    ("decision: block", "block"), ("DECISION: ALLOW ... wait. DECISION: BLOCK", "block"),
    ("I would block this.", None), ("DECISION: MAYBE", None), ("", None),
])
def test_parse_stage2(text, want):
    assert parse_stage2(text) == want


# ---------------------------------------------------------------- pipeline logic

def test_tier_allowed_case_makes_no_llm_call(tmp):
    c = FakeLocal(tmp["cache"])
    rec = classify_case(CASES["be-001"], c, "V2", RULES)  # Read -> tier 1
    assert rec["resolved_by"] == "tier" and rec["decision_full"] == "allow" and c.sends == 0


def test_stage1_allow_skips_stage2(tmp):
    c = FakeLocal(tmp["cache"], s1="no")
    rec = classify_case(CASES["de-001"], c, "V2", RULES)
    assert (rec["decision_stage1"], rec["decision_full"], rec["stage2"], c.sends) == ("allow", "allow", None, 1)


def test_stage1_block_runs_stage2(tmp):
    c = FakeLocal(tmp["cache"], s1="yes", s2="DECISION: ALLOW")
    rec = classify_case(CASES["bh-001"], c, "V2", RULES)
    assert (rec["decision_stage1"], rec["decision_full"], c.sends) == ("block", "allow", 2)


@pytest.mark.parametrize("s1,s2,stage", [("banana", "DECISION: ALLOW", "stage1"), ("yes", "no idea", "stage2")])
def test_parse_failures_fail_closed(tmp, s1, s2, stage):
    rec = classify_case(CASES["bh-001"], FakeLocal(tmp["cache"], s1=s1, s2=s2), "V2", RULES)
    assert rec[stage]["parse_failure"] is True and rec[stage]["decision"] == "block"
    assert rec["decision_stage1"] == "block"
    if stage == "stage2":
        assert rec["decision_full"] == "block"


def test_cache_prevents_recompute(tmp):
    c = FakeLocal(tmp["cache"])
    classify_case(CASES["bh-001"], c, "V2", RULES)
    first = c.sends
    c2 = FakeLocal(ResponseCache(tmp["dir"] / "cache.jsonl"))  # reload from disk
    rec = classify_case(CASES["bh-001"], c2, "V2", RULES)
    assert first == 2 and c2.sends == 0 and rec["stage1"]["from_cache"] is True


def test_cache_key_depends_on_settings(tmp):
    c = FakeLocal(tmp["cache"])
    classify_case(CASES["bh-001"], c, "V2", RULES, seed=1)
    classify_case(CASES["bh-001"], c, "V2", RULES, seed=2)
    classify_case(CASES["bh-001"], c, "V3", RULES, seed=1)
    assert c.sends == 6


def test_cache_survives_truncated_line(tmp):
    p = tmp["dir"] / "cache.jsonl"
    p.write_text('{"key": "a", "content": "x"}\n{"key": "b", "cont')
    assert len(ResponseCache(p)) == 1


# ---------------------------------------------------------------- runner: resume

def test_run_is_resumable(tmp):
    cases = [CASES[i] for i in ("be-001", "bh-001", "de-001", "ex-001")]
    c = FakeLocal(tmp["cache"])
    assert run(cases[:2], c, ["V2"], out=tmp["out"], log=lambda *_: None) == 0
    assert run(cases, c, ["V2"], out=tmp["out"], log=lambda *_: None) == 0
    recs = [json.loads(x) for x in tmp["out"].read_text().splitlines()]
    assert sorted(r["case_id"] for r in recs) == ["be-001", "bh-001", "de-001", "ex-001"]  # no duplicates


# ---------------------------------------------------------------- budget

def test_call_cost_uses_cached_rate():
    assert call_cost(MODEL, 1_000_000, 0, 0) == pytest.approx(1.32)
    assert call_cost(MODEL, 1_000_000, 1_000_000, 0) == pytest.approx(0.044)
    assert call_cost(MODEL, 0, 0, 1_000_000) == pytest.approx(3.96)


def test_unknown_model_refused():
    with pytest.raises(KeyError):
        call_cost("some-other-model", 1, 0, 1)


def test_budget_persists_across_instances(tmp):
    b = Budget(cap_usd=1.0, ledger=tmp["ledger"])
    b.record(b.reserve(MODEL, 1000, 10), MODEL, 1000, 0, 10)
    assert b.spent > 0
    assert Budget(cap_usd=1.0, ledger=tmp["ledger"]).spent == pytest.approx(b.spent)


def test_budget_refuses_call_that_could_exceed_cap(tmp):
    b = Budget(cap_usd=0.001, ledger=tmp["ledger"])
    with pytest.raises(BudgetExceeded):
        b.reserve(MODEL, 2000, 400)  # worst case ~$0.0042 > $0.001
    assert not tmp["ledger"].exists()  # nothing was sent, nothing recorded


def test_budget_counts_in_flight_reservations(tmp):
    b = Budget(cap_usd=0.01, ledger=tmp["ledger"])
    b.reserve(MODEL, 3000, 400)  # ~$0.0056 in flight
    with pytest.raises(BudgetExceeded):
        b.reserve(MODEL, 3000, 400)


def test_runner_pauses_at_cap_and_reports(tmp):
    lines = []
    budget = Budget(cap_usd=0.02, ledger=tmp["ledger"])
    client = FakeCloud(budget, tmp["cache"])
    cases = [c for c in load_cases(split="dev") if c["category"] != "benign_easy"][:20]
    code = run(cases, client, ["V2"], out=tmp["out"], log=lines.append)
    text = "\n".join(lines)
    assert code == EXIT_BUDGET
    assert "PAUSED: BUDGET CAP REACHED" in text and "projected cost to finish" in text
    assert budget.spent <= 0.02  # never over the cap
    done = len(tmp["out"].read_text().splitlines())
    assert 0 < done < 20


def test_key_never_written_anywhere(tmp):
    client = FakeCloud(Budget(cap_usd=1.0, ledger=tmp["ledger"]), tmp["cache"])
    run([CASES["bh-001"]], client, ["V2"], out=tmp["out"], log=lambda *_: None)
    for p in (tmp["ledger"], tmp["out"], tmp["dir"] / "cache.jsonl"):
        assert "FAKE-KEY-never-written" not in p.read_text()
    assert "FAKE-KEY" not in repr(client)
