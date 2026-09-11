#!/usr/bin/env bash
# bisim demo — behavior-addressed code in six steps. Steps 1–3 and 5–6 are offline.
set -u
cd "$(dirname "$0")/.."
B=".venv/bin/bisim"; [ -x "$B" ] || B="bisim"
ROOT="$(mktemp -d)"; mkdir -p "$ROOT/.bisim"
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "1. hash: a function's behavioral address"
$B hash examples/median_v1.py:median --root "$ROOT"

step "2. diff: a refactor keeps the address (exit 0)"
$B diff examples/median_v1.py:median examples/median_v2.py:median --root "$ROOT"; echo "exit=$?"

step "3. diff: a different reading of 'median' changes it, with the input that proves it (exit 1)"
$B diff examples/median_v1.py:median examples/median_v3.py:median --root "$ROOT"; echo "exit=$?"

step "4. witness: a human fixes the intent on the ambiguous input"
$B witness add examples/median_v1.py:median --input '[[1.0, 2.0, 3.0, 4.0]]' --expect 2.5 --note "even length: mean of the two middle values" --root "$ROOT"

step "5. diff again: now it is an intent violation, not just a change (exit 2)"
$B diff examples/median_v1.py:median examples/median_v3.py:median --root "$ROOT"; echo "exit=$?"

step "6. mint: discover intent from scratch by asking only where implementations disagree"
if [ -n "${ANTHROPIC_API_KEY:-}" ] || command -v claude >/dev/null 2>&1; then
  echo "(interactive — answer the questions; Ctrl-D stops)"
  $B mint --intent "median of a list of numbers" --sig "def median(xs: list[float]) -> float" \
     --out "$ROOT/median.py" --tests-dir "$ROOT/tests" --root "$ROOT"
else
  echo "skipped: set ANTHROPIC_API_KEY (pip install 'bisim[llm]') or install Claude Code to run mint"
fi
echo; echo "scratch root: $ROOT"
