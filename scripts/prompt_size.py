"""Measure real prompt sizes with the local model (free, no cloud calls).

For each variant, sends the stage-2 prompt of the longest case (by characters) to llama3.2:3b with
num_predict=1 and reads prompt_eval_count. Also measures the fixed prefix alone. Checks that every
prompt leaves room for stage 2's 400 output tokens inside num_ctx=4096.

    .venv/bin/python scripts/prompt_size.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guard.data import load_cases  # noqa: E402
from guard.render import VARIANTS, build_messages, fixed_prompt  # noqa: E402

URL = "http://localhost:11434/api/chat"
NUM_CTX, STAGE2_OUT = 4096, 400


def count(messages) -> int:
    r = requests.post(URL, json={"model": "llama3.2:3b", "messages": messages, "stream": False,
                                 "options": {"num_predict": 1, "num_ctx": 8192, "num_thread": 4, "temperature": 0}},
                      timeout=900)
    r.raise_for_status()
    return r.json()["prompt_eval_count"]


def main() -> None:
    cases = load_cases()
    prefix = count([{"role": "system", "content": fixed_prompt()}, {"role": "user", "content": "x"}])
    print(f"fixed prefix (system message + minimal user turn): {prefix} tokens, {len(fixed_prompt())} chars")
    worst = 0
    for v in VARIANTS:
        lens = sorted(((len(build_messages(c, v, 2)[1]["content"]), c["id"]) for c in cases), reverse=True)
        median_chars = lens[len(lens) // 2][0]
        chars, cid = lens[0]
        case = next(c for c in cases if c["id"] == cid)
        n = count(build_messages(case, v, 2))
        worst = max(worst, n)
        print(f"{v:16s} longest case {cid}: {n} prompt tokens ({chars} transcript chars; median {median_chars})"
              f" -> {n + STAGE2_OUT} with stage-2 output")
    verdict = "OK" if worst + STAGE2_OUT <= NUM_CTX else "TOO BIG"
    print(f"\nworst case {worst} + {STAGE2_OUT} output = {worst + STAGE2_OUT} / num_ctx {NUM_CTX}: {verdict}")


if __name__ == "__main__":
    main()
