"""08 · What it costs: tokens, prompt caching, dollars and seconds per decision.

    .venv/bin/python examples/08_cost_latency.py

Cloud rates (peak, USD per 1M tokens): input 1.32 · cached input 0.044 · output 3.96.
Replies served from our local response cache cost nothing again, so they are left out of cost and latency.
"""
import json
import statistics

from _common import MODELS, NICK, ROOT, V, done, frozen_decisions, parse_args, table, write_results

from analysis.report import pct

args = parse_args(__doc__)
D = frozen_decisions()
RATE = {"input": 1.32, "cached": 0.044, "output": 3.96}

# 1. Collect every fresh model call (not served from the local response cache) per model and stage.
calls = {(m, s): [] for m in MODELS for s in ("stage1", "stage2")}
for m in MODELS:
    for v in V:
        for r in D[(m, v)]:
            for s in ("stage1", "stage2"):
                x = r.get(s)
                if isinstance(x, dict) and not x["from_cache"]:
                    calls[(m, s)].append(x)

# 2. Cloud cost anatomy: uncached input vs cached input (provider prompt cache) vs output.
ds = [x for s in ("stage1", "stage2") for x in calls[(MODELS[0], s)]]
P, C, O = (sum(x[k] for x in ds) for k in ("prompt_tokens", "cached_tokens", "output_tokens"))
parts = {"uncached input": (P - C) * RATE["input"], "cached input": C * RATE["cached"], "output": O * RATE["output"]}
total = sum(parts.values()) / 1e6
no_cache = (P * RATE["input"] + O * RATE["output"]) / 1e6
spend = [json.loads(l) for l in (ROOT / "results" / "cloud_spend.jsonl").open()]
ledger = sum(x["cost_usd"] for x in spend if x["ts"] >= "2026-11-08T08:3")   # the frozen test run
project = sum(x["cost_usd"] for x in spend)                                     # env check + tuning + test
actions = sum(len(D[(MODELS[0], v)]) for v in V)
rows = [[k, f"{t:,}", f"${usd / 1e6:.3f}", f"{usd / 1e6 / total:.0%}"]
        for k, t, usd in (("uncached input", P - C, parts["uncached input"]), ("cached input", C, parts["cached input"]),
                          ("output", O, parts["output"]))]
rows.append(["total", f"{P + O:,}", f"${total:.3f}", "100%"])
print(f"DeepSeek test run: {len(ds)} fresh calls for {actions} actions (prompt-cache hit rate {C / P:.0%})")
print(table(rows, ["tokens", "count", "cost", "share"]))
print(f"ledger says ${ledger:.4f} · per 1,000 actions ${total / actions * 1000:.2f} · "
      f"with no prompt caching ${no_cache:.2f}")
print(f"whole project so far (env check, tuning, test run): ${project:.3f} of the $5.00 cap")

# 3. Latency per call, median and p95, per model and stage.
lat_rows, lat_out = [], []
for m in MODELS:
    for s in ("stage1", "stage2"):
        xs = [x["latency_s"] for x in calls[(m, s)]]
        toks = statistics.mean(x["output_tokens"] for x in calls[(m, s)])
        lat_rows.append([NICK[m], s, len(xs), f"{statistics.median(xs):.1f}", f"{pct(xs, 0.95):.1f}", f"{toks:.0f}"])
        lat_out.append({"model": m, "stage": s, "calls": len(xs), "median_s": statistics.median(xs),
                        "p95_s": pct(xs, 0.95), "mean_output_tokens": toks})
print()
print(table(lat_rows, ["model", "stage", "calls", "median s", "p95 s", "output tokens"], "llrrrr"))

done(write_results("e6_cost", [{"part": k, "usd": v / 1e6} for k, v in parts.items()]
                   + [{"part": "total", "usd": total}, {"part": "no_prompt_cache", "usd": no_cache},
                      {"part": "ledger", "usd": ledger}, {"part": "project_total", "usd": project}, {"part": "per_1000_actions", "usd": total / actions * 1000}],
                   args, {"rates_per_1M": RATE, "prompt_tokens": P, "cached_tokens": C, "output_tokens": O}))
done(write_results("e6_latency", lat_out, args))
