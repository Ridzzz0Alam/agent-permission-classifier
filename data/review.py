"""Human review of the drafted cases. DISPLAY ONLY: this tool never executes any command.

Files
  data/cases_draft.jsonl   drafts from build_cases.py (regenerable)
  data/review_log.jsonl    your decisions, appended as you go (resumable; the latest entry per id wins)
  data/cases.jsonl         drafts + your decisions, written by `apply`
  data/FROZEN.json         written by `freeze`; after that, `apply` refuses to change cases.jsonl

Usage (from the project root)
  .venv/bin/python data/review.py stats
  .venv/bin/python data/review.py review [--category C] [--split dev|test] [--redo]
  .venv/bin/python data/review.py show ID
  .venv/bin/python data/review.py export-csv data/review.csv
  .venv/bin/python data/review.py import-csv data/review.csv
  .venv/bin/python data/review.py apply
  .venv/bin/python data/review.py freeze
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from guard.tiers import load_allow_rules, route  # noqa: E402

DRAFT = ROOT / "data" / "cases_draft.jsonl"
LOG = ROOT / "data" / "review_log.jsonl"
FINAL = ROOT / "data" / "cases.jsonl"
FROZEN = ROOT / "data" / "FROZEN.json"

CATEGORIES = {"benign_easy": "allow", "benign_hard": "allow", "destructive": "block",
              "exfiltration": "block", "prompt_injection": "block", "overeager": "block"}
THREATS = {"allow": ["none"], "block": ["overeager", "honest_mistake", "prompt_injection"]}

_TTY = sys.stdout.isatty()


def col(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


# ---------------------------------------------------------------- data

def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def drafts() -> list[dict]:
    if not DRAFT.exists():
        sys.exit("No drafts found. Run: .venv/bin/python data/build_cases.py")
    return load_jsonl(DRAFT)


def decisions() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for e in load_jsonl(LOG):
        latest[e["id"]] = e
    return latest


def log_decision(entry: dict) -> None:
    entry["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOG.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def merged(case: dict, dec: dict | None) -> dict:
    out = dict(case)
    if dec and dec["decision"] == "edit":
        for k in ("label", "category", "threat_source", "rationale"):
            if dec.get(k):
                out[k] = dec[k]
    out["review"] = {"status": {"accept": "accepted", "edit": "edited"}.get(dec["decision"], "unreviewed")
                     if dec else "unreviewed", "note": (dec or {}).get("note", "")}
    return out


# ---------------------------------------------------------------- display

def fmt_call(call: dict) -> str:
    t, i = call["tool"], call["input"]
    if t == "Bash":
        return f"Bash: {i['command']}"
    if t == "Edit":
        return (f"Edit {i['file_path']}\n      - {i['old_string']!r}\n      + {i['new_string']!r}")
    if t == "Write":
        body = textwrap.indent(i["content"].rstrip(), "      | ")
        return f"Write {i['file_path']}\n{body}"
    if t == "Read":
        return f"Read {i['file_path']}"
    if t == "WebFetch":
        return f"WebFetch {i['url']}"
    if t == "SpawnSubagent":
        return f"SpawnSubagent ({i.get('description', '')}): {i['prompt']}"
    return f"{t} {json.dumps(i, ensure_ascii=False)}"


def one_line(call: dict) -> str:
    return " ".join(fmt_call(call).split())


def show(case: dict, dec: dict | None, tier_rules, idx: str = "") -> None:
    c = merged(case, dec)
    d = route(case["action"], case["project_dir"], tier_rules, case["prior_tool_calls"])
    lab = col(c["label"].upper(), "1;31" if c["label"] == "block" else "1;32")
    print("\n" + "=" * 100)
    print(f"{idx}{col(c['id'], '1')}  split={c['split']}  category={c['category']}  "
          f"threat={c['threat_source']}  label={lab}  review={c['review']['status']}")
    print(f"project: {c['project_dir']}")
    print(f"env: {c['trusted_environment']}")
    print(col(f"tier router: tier {d.tier} ({'allowed without LLM' if d.allowed else 'to classifier'}) - {d.reason}", "2"))
    print(col("\nUSER:", "1;36"))
    for m in c["user_messages"]:
        print(f"  > {m}")
    if c["prior_tool_calls"]:
        print(col("\nPRIOR TOOL CALLS:", "1;36"))
        for n, (call, out) in enumerate(zip(c["prior_tool_calls"], c["tool_outputs"]), 1):
            print(f"  {n}. {fmt_call(call)}")
            if out:
                print(col(textwrap.indent(out, "       [output, V3 only] "), "33"))
    if c["assistant_text"]:
        print(col("\nAGENT TEXT [V3 only]: ", "33") + c["assistant_text"])
    print(col("\nACTION TO CLASSIFY:", "1;35"))
    print("  " + fmt_call(c["action"]).replace("\n", "\n  "))
    print(col("\nRATIONALE: ", "1") + c["rationale"])
    if c["review"]["note"]:
        print(col("NOTE: ", "1") + c["review"]["note"])


# ---------------------------------------------------------------- interactive review

def ask(prompt: str, choices: list[str] | None = None, default: str | None = None) -> str:
    while True:
        try:
            ans = input(prompt).strip()
        except EOFError:
            print()
            sys.exit(0)
        if not ans and default is not None:
            return default
        if choices is None or ans in choices:
            return ans
        print(f"  choose one of: {', '.join(choices)}")


def pick(title: str, options: list[str], current: str) -> str:
    print(f"  {title}:")
    for n, o in enumerate(options, 1):
        print(f"    {n}. {o}{'  (current)' if o == current else ''}")
    n = ask(f"  number [enter = {current}]: ", [str(i) for i in range(1, len(options) + 1)], default="")
    return options[int(n) - 1] if n else current


def edit_flow(c: dict) -> dict | None:
    new_label = "allow" if c["label"] == "block" else "block"
    cats = [k for k, v in CATEGORIES.items() if v == new_label]
    print(f"  flipping label {c['label']} -> {new_label}")
    cat = pick("new category", cats, cats[0])
    threat = pick("threat source", THREATS[new_label], THREATS[new_label][0])
    why = ask("  why (required): ")
    if not why:
        print("  cancelled: a reason is required")
        return None
    rat = ask("  new rationale [enter = keep the old one]: ")
    return {"decision": "edit", "label": new_label, "category": cat, "threat_source": threat,
            "rationale": rat or c["rationale"], "note": why}


def review(args) -> None:
    if FROZEN.exists():
        sys.exit("The dataset is frozen (data/FROZEN.json). Delete that file only if you really mean to unfreeze.")
    rules = load_allow_rules(ROOT / "config" / "allow_rules.yaml").kept
    ids = set(args.ids.split(",")) if args.ids else None
    cases = [c for c in drafts()
             if (not args.category or c["category"] == args.category) and (not args.split or c["split"] == args.split)
             and (ids is None or c["id"] in ids)]
    decs = decisions()
    todo = cases if args.redo else [c for c in cases if c["id"] not in decs]
    print(f"{len(todo)} cases to review ({len(cases) - len(todo)} of {len(cases)} already done). "
          "Decisions are saved immediately; quit any time and rerun to resume.")
    for n, c in enumerate(todo, 1):
        show(c, decs.get(c["id"]), rules, idx=f"[{n}/{len(todo)}] ")
        while True:
            a = ask(col("\n[a]ccept  [f]lip label  [c]ategory/threat  [r]ationale  [n]ote+accept  [s]kip  [q]uit > ", "1"),
                    ["a", "f", "c", "r", "n", "s", "q"])
            cur = merged(c, decs.get(c["id"]))
            if a == "q":
                return stats(None)
            if a == "s":
                break
            entry = None
            if a == "a":
                entry = {"decision": "accept", "note": ""}
            elif a == "n":
                entry = {"decision": "accept", "note": ask("  note: ")}
            elif a == "f":
                entry = edit_flow(cur)
            elif a == "c":
                cats = [k for k, v in CATEGORIES.items() if v == cur["label"]]
                entry = {"decision": "edit", "label": cur["label"],
                         "category": pick("category", cats, cur["category"]),
                         "threat_source": pick("threat source", THREATS[cur["label"]], cur["threat_source"]),
                         "rationale": cur["rationale"], "note": ask("  note (optional): ")}
            elif a == "r":
                entry = {"decision": "edit", "label": cur["label"], "category": cur["category"],
                         "threat_source": cur["threat_source"], "rationale": ask("  new rationale: ") or cur["rationale"],
                         "note": ask("  note (optional): ")}
            if entry:
                entry["id"] = c["id"]
                log_decision(entry)
                decs[c["id"]] = entry
                print(col(f"  saved: {entry['decision']}", "32"))
                break
    print("\nAll cases in this selection are reviewed.")
    stats(None)


# ---------------------------------------------------------------- other commands

def stats(_args) -> None:
    cases, decs = drafts(), decisions()
    print(f"\n{'category':17s} {'dev':>4s} {'test':>5s} {'reviewed':>9s} {'edited':>7s}")
    for cat in CATEGORIES:
        cs = [c for c in cases if c["category"] == cat]
        rv = [c for c in cs if c["id"] in decs]
        ed = [c for c in rv if decs[c["id"]]["decision"] == "edit"]
        print(f"{cat:17s} {sum(c['split'] == 'dev' for c in cs):4d} {sum(c['split'] == 'test' for c in cs):5d} "
              f"{len(rv):9d} {len(ed):7d}")
    final = [merged(c, decs.get(c["id"])) for c in cases]
    labels = Counter(c["label"] for c in final)
    print(f"reviewed {sum(c['id'] in decs for c in cases)}/{len(cases)}   "
          f"labels after review: allow={labels['allow']} block={labels['block']}   "
          f"frozen={'yes' if FROZEN.exists() else 'no'}")


def show_cmd(args) -> None:
    rules = load_allow_rules(ROOT / "config" / "allow_rules.yaml").kept
    for c in drafts():
        if c["id"] == args.id:
            return show(c, decisions().get(c["id"]), rules)
    sys.exit(f"no case {args.id}")


CSV_COLS = ["id", "split", "category", "label", "threat_source", "user_messages", "action", "rationale",
            "decision", "new_label", "new_category", "new_threat_source", "note"]


def export_csv(args) -> None:
    decs = decisions()
    with open(args.path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for c in drafts():
            d = decs.get(c["id"], {})
            w.writerow({"id": c["id"], "split": c["split"], "category": c["category"], "label": c["label"],
                        "threat_source": c["threat_source"], "user_messages": " | ".join(c["user_messages"]),
                        "action": one_line(c["action"]), "rationale": c["rationale"],
                        "decision": d.get("decision", ""),
                        "new_label": d.get("label", "") if d.get("decision") == "edit" else "",
                        "new_category": d.get("category", "") if d.get("decision") == "edit" else "",
                        "new_threat_source": d.get("threat_source", "") if d.get("decision") == "edit" else "",
                        "note": d.get("note", "")})
    print(f"Wrote {args.path}. Fill 'decision' with accept or edit (for edit, also new_label/new_category/"
          "new_threat_source and a note), then run import-csv.")


def import_csv(args) -> None:
    by_id = {c["id"]: c for c in drafts()}
    n, errors = 0, []
    with open(args.path, newline="") as f:
        for row in csv.DictReader(f):
            dec = (row.get("decision") or "").strip().lower()
            if not dec:
                continue
            cid = row["id"]
            if cid not in by_id:
                errors.append(f"{cid}: unknown id")
                continue
            if dec == "accept":
                entry = {"id": cid, "decision": "accept", "note": row.get("note", "")}
            elif dec == "edit":
                lab, cat = row["new_label"].strip(), row["new_category"].strip()
                thr = row["new_threat_source"].strip() or ("none" if lab == "allow" else "overeager")
                if CATEGORIES.get(cat) != lab or thr not in THREATS.get(lab, []):
                    errors.append(f"{cid}: inconsistent label/category/threat ({lab}/{cat}/{thr})")
                    continue
                if not row.get("note", "").strip():
                    errors.append(f"{cid}: an edit needs a note explaining why")
                    continue
                entry = {"id": cid, "decision": "edit", "label": lab, "category": cat, "threat_source": thr,
                         "rationale": by_id[cid]["rationale"], "note": row["note"]}
            else:
                errors.append(f"{cid}: decision must be accept or edit, got {dec!r}")
                continue
            log_decision(entry)
            n += 1
    print(f"Imported {n} decisions.")
    if errors:
        print("Skipped rows:\n  " + "\n  ".join(errors))


def apply(_args) -> None:
    if FROZEN.exists():
        sys.exit("The dataset is frozen; refusing to rewrite data/cases.jsonl.")
    decs = drafts(), decisions()
    final = [merged(c, decs[1].get(c["id"])) for c in decs[0]]
    FINAL.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in final))
    print(f"Wrote {FINAL.relative_to(ROOT)}: {len(final)} cases, "
          f"{sum(c['review']['status'] != 'unreviewed' for c in final)} reviewed.")


def freeze(_args) -> None:
    if FROZEN.exists():
        sys.exit("Already frozen.")
    apply(None)
    final = load_jsonl(FINAL)
    unreviewed = [c["id"] for c in final if c["review"]["status"] == "unreviewed"]
    if unreviewed:
        sys.exit(f"Cannot freeze: {len(unreviewed)} cases are unreviewed, e.g. {unreviewed[:5]}")
    bad = [c["id"] for c in final if CATEGORIES[c["category"]] != c["label"]]
    if bad:
        sys.exit(f"Cannot freeze: label/category mismatch in {bad}")
    digest = hashlib.sha256(FINAL.read_bytes()).hexdigest()
    counts = Counter(f"{c['split']}/{c['category']}" for c in final)
    FROZEN.write_text(json.dumps({"sha256": digest, "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "n_cases": len(final), "counts": dict(sorted(counts.items()))}, indent=2) + "\n")
    print(f"Frozen. sha256={digest}\nWrote {FROZEN.relative_to(ROOT)}. "
          "Tag it in git when commits are allowed (git tag dataset-v1).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats").set_defaults(fn=stats)
    r = sub.add_parser("review")
    r.add_argument("--category", choices=list(CATEGORIES))
    r.add_argument("--split", choices=["dev", "test"])
    r.add_argument("--ids", help="comma-separated case ids, e.g. de-020,de-021")
    r.add_argument("--redo", action="store_true", help="also show cases you already reviewed")
    r.set_defaults(fn=review)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=show_cmd)
    e = sub.add_parser("export-csv")
    e.add_argument("path")
    e.set_defaults(fn=export_csv)
    i = sub.add_parser("import-csv")
    i.add_argument("path")
    i.set_defaults(fn=import_csv)
    sub.add_parser("apply").set_defaults(fn=apply)
    sub.add_parser("freeze").set_defaults(fn=freeze)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    import logging
    logging.disable(logging.WARNING)  # hide the "dropped rule" warnings while reviewing
    main()
