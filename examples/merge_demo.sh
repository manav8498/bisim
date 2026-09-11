#!/usr/bin/env bash
# Two edits in different hunks of the same file. Git merges them cleanly.
# Neither author intended the merged behavior. bisim sees it before the merge.
set -u
B="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/bisim"; [ -x "$B" ] || B="bisim"
T=$(mktemp -d); cd "$T"; git init -q -b main
cat > fees.py <<'PY'
def _fee(amount: float) -> float:
    return 1.0


def total(amount: float) -> float:
    return amount + _fee(amount)
PY
git add . && git -c user.name=t -c user.email=t@t commit -qm base
git checkout -q -b alice && sed -i.bak 's/    return 1.0/    return 3.0/' fees.py && rm fees.py.bak && git -c user.name=t -c user.email=t@t commit -qam "alice: the fee is 3 now"
git checkout -q -b bob main && sed -i.bak 's/    return amount + _fee(amount)/    return amount + 2 * _fee(amount)/' fees.py && rm fees.py.bak && git -c user.name=t -c user.email=t@t commit -qam "bob: apply the fee twice"
git checkout -q alice
printf '\n\033[1m1. bisim merge-check main --ours alice --theirs bob\033[0m\n'
"$B" merge-check main --ours alice --theirs bob; echo "exit=$?"
printf '\n\033[1m2. git merge bob\033[0m\n'
git merge --no-edit bob 2>&1 | grep -E "Merge made|CONFLICT"
printf '\n\033[1m3. bisim check --base main   (the merged tree vs base)\033[0m\n'
"$B" check --base main; echo "exit=$?"
echo; echo "scratch repo: $T"
