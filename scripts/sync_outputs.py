"""Copy real program output into ARTICLE.md, so no output is ever typed by hand.

    .venv/bin/python scripts/sync_outputs.py

ARTICLE.md marks each output block like this:

    <!-- OUTPUT 04_headline -->
    ...anything (replaced)...
    <!-- /OUTPUT -->

and the block is filled from results/stdout/04_headline.txt (written by scripts/run_all.sh).
The footer marker <!-- RUNINFO --> gets the date, commit and CPU from results/stdout/run_info.txt.
The only lines dropped are the trailing "wrote results/<file>.csv" notices, which name timestamped
files and would make every re-run look like a content change.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLE = ROOT / "ARTICLE.md"
STDOUT = ROOT / "results" / "stdout"


def output_block(name: str) -> str:
    lines = (STDOUT / f"{name}.txt").read_text().splitlines()
    lines = [l for l in lines if not l.startswith("wrote results/")]
    while lines and not lines[-1].strip():
        lines.pop()
    return "```text\n#### OUTPUT ####\n" + "\n".join(lines) + "\n```"


def main():
    if not ARTICLE.exists():  # the article is not part of the public repo
        print("no ARTICLE.md here: nothing to sync")
        return
    s = ARTICLE.read_text()
    names = re.findall(r"<!-- OUTPUT (\S+) -->", s)
    missing = [n for n in names if not (STDOUT / f"{n}.txt").exists()]
    if missing:
        raise SystemExit(f"no stdout for {missing}: run scripts/run_all.sh first")
    s = re.sub(r"(<!-- OUTPUT (\S+) -->\n).*?(<!-- /OUTPUT -->)",
               lambda m: f"{m.group(1)}{output_block(m.group(2))}\n{m.group(3)}", s, flags=re.S)
    info = dict(l.split(":", 1) for l in (STDOUT / "run_info.txt").read_text().splitlines() if ":" in l)
    run = f"run {info['date'].strip()[:10]}, commit {info['commit'].strip()}, {info['cpu'].strip()}"
    s = re.sub(r"<!-- RUNINFO -->.*?<!-- /RUNINFO -->", f"<!-- RUNINFO -->{run}<!-- /RUNINFO -->", s)
    ARTICLE.write_text(s)
    print(f"synced {len(names)} output blocks into ARTICLE.md")


if __name__ == "__main__":
    main()
