"""09 · Edge cases found along the way, each backed by the saved run.

    .venv/bin/python examples/09_edge_cases.py
"""
import re
from collections import Counter

from _common import MODELS, V, done, frozen_decisions, parse_args, write_results

args = parse_args(__doc__)
D = frozen_decisions()
llama, deepseek = MODELS[1], MODELS[0]
rows = []


def add(n, title, evidence):
    rows.append({"n": n, "edge_case": title, "evidence": evidence})
    print(f"{n}. {title}\n   {evidence}")


# 1. Format failures: llama's stage-2 replies without a parseable "DECISION:" line (blocked by default).
s2 = [r["stage2"] for v in V for r in D[(llama, v)] if r.get("stage2")]
fails = [x for x in s2 if x["parse_failure"]]
kinds = Counter("ran into the 800-token limit" if x["output_tokens"] >= 795 else "said 'the decision is X' instead"
                for x in fails)
add(1, "A 3B model breaks the output format",
    f"{len(fails)}/{len(s2)} llama stage-2 replies unparseable: " + ", ".join(f"{n} {k}" for k, n in kinds.items())
    + "; DeepSeek: 0")

# 2. The strict stage-1 parser takes the first word: 'yes (block' still parses.
s1 = Counter((m, r["stage1"]["raw"]) for m in MODELS for v in V for r in D[(m, v)] if r.get("stage1"))
odd = {(m, k): n for (m, k), n in s1.items() if k not in ("yes", "no")}
add(2, "Stage 1 echoes the instruction", f"{sum(odd.values())} stage-1 replies were not one word: " +
    ", ".join(f"{m.split(':')[0].split('-')[0]} {k!r} ×{n}" for (m, k), n in odd.items()) +
    " (cut at 3 tokens; the first word still parses)")

# 3. Tier 2 lets an injected CI change through without any classifier call.
r = next(r for r in D[(deepseek, "V2")] if r["case_id"] == "pi-003")
add(3, "Tier 2 trusts CI workflow edits", f"pi-003 ({r['category']}), tier {r['tier']}: {r['tier_reason']}")

# 4. The one DeepSeek false positive: explicit user consent vs a hard block rule.
bh = {v: next(r for r in D[(deepseek, v)] if r["case_id"] == "bh-047") for v in V}
rule = re.search(r"\b([A-Z]\d)\b", bh["V2"]["stage2"]["raw"])
add(4, "Consent vs a block rule (bh-047)",
    f"user asked for two narrow permission rules; DeepSeek cites {rule.group(1) if rule else '?'}: "
    + ", ".join(f"{v} {bh[v]['decision_full'].upper()}" for v in V))

# 5. Counting money twice: replies from the local response cache keep the original call's cost.
cached_cost = sum(r[s]["cost_usd"] for v in V for r in D[(deepseek, v)] for s in ("stage1", "stage2")
                  if r.get(s) and r[s]["from_cache"])
fresh = sum(r[s]["cost_usd"] for v in V for r in D[(deepseek, v)] for s in ("stage1", "stage2")
            if r.get(s) and not r[s]["from_cache"])
add(5, "The runner's cost line double-counts cache hits",
    f"naive sum ${fresh + cached_cost:.4f} vs real ${fresh:.4f} (ledger); ${cached_cost:.4f} was never spent")

# 6. The post's 6.6% stage-1 overeager FNR on n=52 is not a whole count.
near = sorted(range(53), key=lambda k: abs(k / 52 - 0.066))[:2]
add(6, "The post's 6.6% of n=52 is not a whole count",
    " vs ".join(f"{k}/52 = {k / 52:.1%}" for k in sorted(near)) + "; 17% fits 9/52 = 17.3%")

done(write_results("e7_edge_cases", rows, args))
