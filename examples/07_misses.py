"""07 · Why the classifier misses: danger not recognised, or consent misread?

    .venv/bin/python examples/07_misses.py

Every dangerous action the full pipeline allowed, labelled from the stage-2 reasoning.
Claude proposed the labels; the project owner reviewed all of them (analysis/miss_labels.json).
  A  = danger not recognised        S1 = stage 1 said "allow", no reasoning to inspect (counts as A)
  B  = danger seen, consent misread  T  = allowed by the tier router, no model call
"""
from collections import Counter

from _common import MODELS, NICK, V, done, frozen_decisions, parse_args, table, write_results

from analysis.report import load_miss_labels, miss_label

args = parse_args(__doc__)
D = frozen_decisions()
labels = load_miss_labels()

rows, out = [], []
for m in MODELS:
    for v in V:
        # 1. Misses = dangerous (label block) but the full pipeline said allow.
        fns = [r for r in D[(m, v)] if r["label"] == "block" and r["decision_full"] == "allow"]
        # 2. The owner's final label where set, else the proposal (all 13 reasoned misses are reviewed).
        kinds = Counter()
        for r in fns:
            prop, fin, note = miss_label(r, v, labels)
            kinds[fin or prop] += 1
            out.append({"model": m, "variant": v, "case_id": r["case_id"], "category": r["category"],
                        "label": fin or prop, "proposed": prop, "reviewed": bool(fin), "note": note})
        rows.append([NICK[m], v, len(fns), kinds["A"] + kinds["S1"], kinds["S1"], kinds["B"], kinds["T"]])
print(table(rows, ["model", "var", "misses", "A total", "of which S1", "B", "T"], "llrrrrr"))

# 3. The consent misreads, the failure mode the post highlights.
print("\nB (danger seen, consent misread):")
for r in out:
    if r["label"] == "B":
        print(f"  {NICK[r['model']]:<11} {r['variant']}  {r['case_id']:<7} {r['category']}")

done(write_results("e5_misses", out, args))
