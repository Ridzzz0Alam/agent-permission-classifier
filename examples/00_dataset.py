"""00 · The dataset: 200 hand-reviewed agent sessions, frozen by hash.

    .venv/bin/python examples/00_dataset.py [--case ov-019]

Commands inside the cases are DATA. They are printed, never executed.
"""
import json
from collections import Counter

from _common import ROOT, done, parse_args, table, write_results

from guard.data import load_cases  # verifies data/cases.jsonl against data/FROZEN.json

args = parse_args(__doc__, lambda ap: ap.add_argument("--case", default="ov-019"))

# 1. Load every case. load_cases() refuses to run if the file's sha256 differs from the frozen one.
cases = load_cases()
frozen = json.loads((ROOT / "data" / "FROZEN.json").read_text())

# 2. Count cases per category and split; the label follows from the category.
order = ["benign_easy", "benign_hard", "destructive", "exfiltration", "prompt_injection", "overeager"]
n = Counter((c["category"], c["split"]) for c in cases)
label = {c["category"]: c["label"] for c in cases}
rows = [[cat, label[cat], n[(cat, "dev")], n[(cat, "test")]] for cat in order]
rows.append(["total", "", sum(n[(c, "dev")] for c in order), sum(n[(c, "test")] for c in order)])
print(table(rows, ["category", "label", "dev", "test"], "llrr"))
print(f"\nsha256 {frozen['sha256'][:16]}… matches data/FROZEN.json (frozen {frozen['frozen_at'][:10]})")

# 3. Show the shape of one case: what the user said, what the agent did before, the action to judge.
c = next(x for x in cases if x["id"] == args.case)
print(f"\ncase {c['id']} ({c['category']}, label={c['label']}):")
print(f"  user:   {c['user_messages'][0][:90]}")
for call in c["prior_tool_calls"]:
    print(f"  before: {json.dumps(call)[:90]}")
print(f"  action: {json.dumps(c['action'])[:90]}")

done(write_results("e0_dataset", [{"category": r[0], "label": r[1], "dev": r[2], "test": r[3]} for r in rows],
                   args, {"showcase_case": args.case}))
