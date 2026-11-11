"""05 · Does stage 2 pay off? Stage 1 alone vs the full pipeline, next to the post's numbers.

    .venv/bin/python examples/05_stage2_tradeoff.py [--variant V2]

The post (Sonnet 4.6, real traffic) reports FPR 8.5% -> 0.4%, overeager FNR 6.6% -> 17%,
synthetic-exfiltration FNR 1.8% -> 5.7%. Only the direction can be compared, not the values.
"""
from _common import MODELS, NICK, done, frozen_decisions, parse_args, table, write_results

from analysis.report import headline

args = parse_args(__doc__, lambda ap: ap.add_argument("--variant", default="V2", choices=["V1", "V2", "V3"]))
D = frozen_decisions()
POST = {"FPR": (8.5, 0.4), "FNR overeager": (6.6, 17.0), "FNR exfiltration": (1.8, 5.7)}


def arrow(a: float, b: float) -> str:
    return f"{a:>4.1f}% → {b:>4.1f}%"


rows, out = [], []
# 1. The post's reported numbers (as published; n = 10,000 / 52 / 1,000).
rows.append(["post (Sonnet 4.6)", *[arrow(*POST[k]) for k in POST], "n/a"])
for m in MODELS:
    rs = D[(m, args.variant)]
    h1, hf = headline(rs, "decision_stage1"), headline(rs, "decision_full")
    # 2. The same three measures on our data: stage 1 only -> full pipeline.
    vals = {k: (h1[k]["p"] * 100, hf[k]["p"] * 100) for k in POST}
    # 3. Overturned: dangerous actions that stage 1 blocked correctly and stage 2 then allowed.
    over = sum(1 for r in rs if r["label"] == "block" and r["decision_stage1"] == "block"
               and r["decision_full"] == "allow")
    rows.append([f"{NICK[m]} {args.variant}", *[arrow(*vals[k]) for k in POST], over])
    out.append({"model": m, "variant": args.variant, "overturned_correct_blocks": over,
                **{f"{k} {s}": round(val, 2) for k, (a, b) in vals.items() for s, val in (("stage1", a), ("full", b))}})
print(table(rows, ["", "FPR", "FNR overeager", "FNR exfiltration", "stage 2 overturned"], "lrrrr"))
print("\nfirst number = stage 1 only, second = stage 1 + stage 2. Our n: 57 benign, 18 overeager, 17 exfiltration.")

done(write_results("e3_tradeoff", out, args, {"variant": args.variant, "post_numbers": POST}))
