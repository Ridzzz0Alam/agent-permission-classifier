#!/usr/bin/env bash
# Reproduce every number, chart and diagram in ARTICLE.md / FINDINGS.md from the saved run.
#   ./scripts/run_all.sh            (about 1-2 min; makes NO model calls and costs nothing)
# Program stdout goes to results/stdout/<name>.txt; scripts/sync_outputs.py copies it into ARTICLE.md.
# Re-running the experiment itself (hours locally, ~$0.90 cloud) is `python -m guard.classify`, see HANDOFF.md.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
OUT=results/stdout
mkdir -p "$OUT"

{
  echo "date:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "commit:    $(git rev-parse --short=12 HEAD)$(git diff --quiet HEAD -- . ':!results' ':!figures' ':!diagrams' ':!ARTICLE.md' ':!title.png' || echo -dirty)"
  echo "python:    $($PY --version 2>&1)"
  echo "cpu:       $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) ($(nproc) threads)"
  echo "os:        $(. /etc/os-release && echo "$PRETTY_NAME"), $(uname -sr)"
  echo "prompt:    $($PY -c 'from guard.render import prompt_fingerprint; print(prompt_fingerprint()[:16])')"
} | tee "$OUT/run_info.txt"

# 1. The full test suite, then the replay validation on its own (its output goes into the article).
$PY -m pytest -q
$PY -m pytest tests/test_replay.py -v -p no:cacheprovider 2>&1 \
  | grep -E 'PASSED|FAILED|ERROR|passed|failed' | sed -E 's/ in [0-9.]+s//' | tee "$OUT/00_test.txt"

# 2. Every example, in article order.
for ex in 00_dataset 01_tier_router 02_prompt 03_two_stage 04_headline 05_stage2_tradeoff 06_variants \
          07_misses 08_cost_latency 09_edge_cases; do
  echo "=== $ex"
  $PY "examples/$ex.py" | tee "$OUT/$ex.txt"
done

# 3. Charts, diagrams, cover, and the article's OUTPUT blocks.
$PY scripts/plot.py
$PY scripts/diagrams.py
cp figures/00-title.png title.png
$PY scripts/sync_outputs.py
