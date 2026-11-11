"""06 · What should the classifier see? V1 (tool calls) vs V2 (+ user) vs V3 (+ agent text, tool outputs).

    .venv/bin/python examples/06_variants.py

The post: tool calls alone gave the lowest FNR but blocked requested actions; adding agent text and
tool results did worse on overeager actions; user + tool calls ("reasoning-blind") was chosen.
"""
from _common import MODELS, NICK, V, done, frozen_decisions, parse_args, table, write_results

from analysis.report import headline

args = parse_args(__doc__)
D = frozen_decisions()


def kn(r: dict) -> str:
    return f"{r['k']}/{r['n']} ({r['p']:.0%})"


rows, out = [], []
for m in MODELS:
    for v in V:
        # 1. Full pipeline, Tier-3 cases. benign_hard = risky-looking actions the user explicitly asked for.
        h = headline(D[(m, v)], "decision_full")
        rows.append([NICK[m], v, kn(h["FPR benign_hard"]), kn(h["FPR benign_easy"]), kn(h["FNR"]),
                     kn(h["FNR overeager"]), kn(h["FNR prompt_injection"])])
        out.append({"model": m, "variant": v, **{f"{k}_{x}": h[k][x] for k in
                    ("FPR", "FPR benign_hard", "FPR benign_easy", "FNR", "FNR overeager", "FNR prompt_injection",
                     "FNR exfiltration", "FNR destructive") for x in ("k", "n", "lo", "hi")}})
print(table(rows, ["model", "var", "FPR hard benign", "FPR easy benign", "FNR all", "FNR overeager",
                   "FNR injection"], "llrrrrr"))
print("\nV1 = tool calls only · V2 = + user messages (the post's choice) · V3 = + agent text and tool outputs")

done(write_results("e4_variants", out, args))
