"""02 · The classifier prompt: one fixed template, three slots, one transcript, one final line.

    .venv/bin/python examples/02_prompt.py [--case ov-019]

Builds the real prompt for one case in all three variants and reports its parts' sizes.
Token counts come from the saved calls of the frozen run (no model call here).
"""
from _common import MODELS, NICK, V, VKEY, done, frozen_decisions, parse_args, stage, table, write_results

from guard.data import load_cases
from guard.render import QUESTION, STAGE_INSTRUCTIONS, SLOTS, build_messages, fixed_prompt, \
    prompt_fingerprint, render_transcript

args = parse_args(__doc__, lambda ap: ap.add_argument("--case", default="ov-019"))
case = next(c for c in load_cases(split="test") if c["id"] == args.case)
D = frozen_decisions()

# 1. The fixed part: template + environment + block rules + allow exceptions. Same for every call,
#    so it sits first and the provider can cache it.
fixed = fixed_prompt()
print(f"fixed prompt: {len(fixed):,} chars, fingerprint {prompt_fingerprint()[:16]}")
for slot, path in SLOTS.items():
    print(f"  slot {slot:<22} {len(path.read_text()):>6,} chars  ({path.name})")

# 2. The case part: the transcript the classifier sees differs per variant.
rows, out = [], []
for v in V:
    tr = render_transcript(case, VKEY[v])
    msgs = build_messages(case, VKEY[v], 1)
    rec = {m: next(r for r in D[(m, v)] if r["case_id"] == args.case) for m in MODELS}
    s1 = {m: stage(rec[m], "stage1") for m in MODELS}
    toks = [f"{s1[m]['prompt_tokens']:,}" if s1[m] else "-" for m in MODELS]
    rows.append([v, f"{len(tr):,}", f"{sum(len(x['content']) for x in msgs):,}", *toks])
    out.append({"case_id": args.case, "variant": v, "transcript_chars": len(tr),
                "total_chars": sum(len(x["content"]) for x in msgs),
                **{f"prompt_tokens_{NICK[m]}": (s1[m] or {}).get("prompt_tokens") for m in MODELS}})
print()
print(table(rows, ["variant", "transcript chars", "total chars", *[f"{NICK[m]} tokens" for m in MODELS]]))

# 3. The only difference between stage 1 and stage 2 is the last instruction.
print(f"\nlast lines: {QUESTION}")
for s, text in STAGE_INSTRUCTIONS.items():
    print(f"  stage {s}: {text}")

done(write_results("e2_prompt_sizes", out, args, {"case": args.case, "fixed_chars": len(fixed)}))
