"""04 · Head to head: a 3B local model vs a cloud model, with 95% Wilson intervals.

    .venv/bin/python examples/04_headline.py [--seed 42] [--draws 20000]

Scores the frozen test run (Tier-3 cases: 57 benign, 69 dangerous) and checks, by a seeded
simulation, that Wilson intervals really cover ~95% at these small sample sizes.
"""
import random

from _common import MODELS, NICK, V, done, frozen_decisions, parse_args, table, write_results

from analysis.report import headline, wilson

args = parse_args(__doc__, lambda ap: ap.add_argument("--draws", type=int, default=20000))
D = frozen_decisions()


def cell(r: dict) -> str:
    return f"{r['k']:>2}/{r['n']} {r['p']:>4.0%} [{r['lo']:>3.0%}-{r['hi']:>3.0%}]"


# 1. FPR = benign blocked / benign; FNR = dangerous allowed / dangerous. Stage 1 alone vs the full pipeline.
rows, out = [], []
for m in MODELS:
    for v in V:
        h1, hf = headline(D[(m, v)], "decision_stage1"), headline(D[(m, v)], "decision_full")
        rows.append([NICK[m], v, cell(h1["FPR"]), cell(h1["FNR"]), cell(hf["FPR"]), cell(hf["FNR"])])
        for pipe, h in (("stage1", h1), ("full", hf)):
            out.append({"model": m, "variant": v, "pipeline": pipe,
                        **{f"{k}_{x}": h[k][x] for k in ("FPR", "FNR") for x in ("k", "n", "p", "lo", "hi")}})
print(table(rows, ["model", "var", "stage 1 FPR", "stage 1 FNR", "full FPR", "full FNR"], "llrrrr"))


# 2. Theory check: how often does the Wilson interval contain the true rate, at our n?
#    Draw k ~ Binomial(n, p) many times and count how often p lies inside wilson(k, n).
rng = random.Random(args.seed)
cov_rows, cov_out = [], []
for n in (57, 69):
    for p in (0.02, 0.17, 0.50):
        hit = 0
        for _ in range(args.draws):
            k = sum(rng.random() < p for _ in range(n))
            lo, hi = wilson(k, n)
            hit += lo <= p <= hi
        cov_rows.append([n, f"{p:.0%}", f"{hit / args.draws:.1%}"])
        cov_out.append({"n": n, "p": p, "draws": args.draws, "coverage": hit / args.draws})
print(f"\nWilson 95% coverage check ({args.draws:,} seeded draws each, seed {args.seed}):")
print(table(cov_rows, ["n", "true rate", "coverage"]))

done(write_results("e2_headline", out, args))
done(write_results("e2_wilson_coverage", cov_out, args))
