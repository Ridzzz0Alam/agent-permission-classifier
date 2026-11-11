"""03 · Two stages, one prompt: replay one case through stage 1 and stage 2, for both models.

    .venv/bin/python examples/03_two_stage.py [--case ov-019] [--variant V2]
    .venv/bin/python examples/03_two_stage.py --live      # re-ask the LOCAL model (needs Ollama, ~1 min, free)

By default it replays the saved replies of the frozen run. --live calls only the local llama3.2:3b,
never the cloud model, and writes to a throw-away cache so the saved results stay untouched.
"""
import tempfile
from pathlib import Path

from _common import MODELS, NICK, VKEY, done, frozen_decisions, parse_args, stage, table, write_results

from guard.classify import STAGE1_MAX_TOKENS, STAGE2_MAX_TOKENS, parse_stage1, parse_stage2


def extra(ap):
    ap.add_argument("--case", default="ov-019")
    ap.add_argument("--variant", default="V2", choices=["V1", "V2", "V3"])
    ap.add_argument("--live", action="store_true", help="call the local model instead of replaying")


args = parse_args(__doc__, extra)


def last_line(text: str) -> str:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return (lines[-1] if lines else "")[:40]


def run_live(case_id: str, variant: str) -> dict:
    # Same call path as the real run: build_messages -> client.chat -> strict parser (fail closed).
    from guard.cache import ResponseCache
    from guard.clients import LocalClient
    from guard.data import load_cases
    from guard.render import build_messages
    case = next(c for c in load_cases(split="test") if c["id"] == case_id)
    client = LocalClient(cache=ResponseCache(Path(tempfile.mkdtemp()) / "live.jsonl"))
    r1 = client.chat(build_messages(case, VKEY[variant], 1), num_predict=STAGE1_MAX_TOKENS, seed=args.seed)
    rec = {"model": client.model, "stage1": {"raw": r1.content, "latency_s": r1.latency_s}, "stage2": None}
    if (parse_stage1(r1.content) or "block") == "block":
        r2 = client.chat(build_messages(case, VKEY[variant], 2), num_predict=STAGE2_MAX_TOKENS, seed=args.seed)
        rec["stage2"] = {"raw": r2.content, "latency_s": r2.latency_s}
    return rec


# 1. Get the record for each model: from the frozen run, or live from the local model.
D = frozen_decisions()
records = [next(r for r in D[(m, args.variant)] if r["case_id"] == args.case) for m in MODELS]
if args.live:
    records = [run_live(args.case, args.variant)]

rows, out = [], []
for r in records:
    s1, s2 = stage(r, "stage1"), stage(r, "stage2")
    if s1 is None:  # settled by Tier 1/2, no model call
        rows.append([NICK[r["model"]], "-", "-", "-", "allow (tier)", "0.0"])
        continue
    # 2. Stage 1: one word. 'yes' = block -> go to stage 2; 'no' = allow; anything else = block.
    d1 = parse_stage1(s1["raw"]) or "block"
    # 3. Stage 2 (only after a stage-1 block): reasoning that must end in 'DECISION: BLOCK|ALLOW'.
    d2 = (parse_stage2(s2["raw"]) or "block") if s2 else None
    final = d2 or d1
    secs = s1["latency_s"] + (s2["latency_s"] if s2 else 0)
    rows.append([NICK[r["model"]], repr(s1["raw"].strip()), d1, last_line(s2["raw"]) if s2 else "(not run)",
                 final, f"{secs:.1f}"])
    out.append({"case_id": args.case, "variant": args.variant, "model": r["model"], "live": args.live,
                "stage1_raw": s1["raw"].strip(), "stage1": d1, "stage2_last_line": last_line(s2["raw"]) if s2 else "",
                "final": final, "seconds": round(secs, 2)})

print(f"case {args.case}, variant {args.variant}, {'LIVE local call' if args.live else 'replayed from the frozen run'}")
print(table(rows, ["model", "stage 1 says", "parsed", "stage 2 ends with", "final", "seconds"], "lllllr"))
done(write_results("e2_two_stage" + ("_live" if args.live else ""), out, args,
                   {"case": args.case, "variant": args.variant, "live": args.live}))
