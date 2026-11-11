"""01 · The tier router: which actions never reach the classifier?

    .venv/bin/python examples/01_tier_router.py

Runs the real router (guard/tiers.py) on all 140 test cases. Pure code, no model call.
"""
import logging
from collections import Counter

from _common import ROOT, done, parse_args, table, write_results

from guard.data import load_cases
from guard.tiers import load_allow_rules, route

args = parse_args(__doc__)
logging.disable(logging.WARNING)  # the router logs each dropped rule; we print them below

# 1. Load the user's always-allow rules. Rules that would allow arbitrary code are dropped here.
loaded = load_allow_rules(ROOT / "config" / "allow_rules.yaml")
cases = load_cases(split="test")

# 2. Route every action: Tier 1 (read-only / always-allow), Tier 2 (edit inside the project),
#    Tier 3 (everything else -> classifier).
out = []
for c in cases:
    t = route(c["action"], c["project_dir"], loaded.kept, c["prior_tool_calls"])
    out.append({"case_id": c["id"], "label": c["label"], "category": c["category"], "tool": c["action"]["tool"],
                "tier": t.tier, "allowed_without_llm": t.allowed, "reason": t.reason})

# 3. Count per tier and label.
n = Counter((r["tier"], r["label"]) for r in out)
rows = [[f"Tier {t}", n[(t, "allow")], n[(t, "block")], f"{(n[(t, 'allow')] + n[(t, 'block')]) / len(out):.0%}",
         "no" if t < 3 else "yes"] for t in (1, 2, 3)]
print(table(rows, ["tier", "benign", "dangerous", "share", "classifier?"]))
print(f"\nalways-allow rules kept: {len(loaded.kept)}; dropped because they allow arbitrary code: {len(loaded.dropped)}")
print("  dropped: " + ", ".join(raw for raw, _ in loaded.dropped))

# 4. Any dangerous action that the router lets through is a miss no classifier can catch.
for r in out:
    if r["tier"] < 3 and r["label"] == "block":
        print(f"dangerous but allowed by Tier {r['tier']}: {r['case_id']} ({r['category']}) - {r['reason']}")

done(write_results("e1_tiers", out, args))
