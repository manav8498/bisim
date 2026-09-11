"""``bisim reach`` command wiring."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict

from .llm import default_client
from .reach import reach


def run_reach(args, target) -> int:
    path, fn = target
    try:
        client = default_client(model=args.model, prefer=args.client)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    r = reach(path, fn, client, root=args.root, rounds=args.rounds, max_inputs=args.max_inputs, timeout=args.timeout)
    if args.json:
        print(json.dumps(asdict(r), indent=1, sort_keys=True, ensure_ascii=False))
        return 0
    if not r.missed_before:
        print(f"{fn}: every executable line is already reached by the probe set")
        return 0
    print(f"{fn}: lines never reached before: {', '.join(map(str, r.missed_before))}")
    for a in r.inputs:
        print(f"  + {tuple(a)!r}" if isinstance(a, list) else f"  + {a}")
    print(f"  added {r.added} suggested probe(s), rejected {r.rejected} proposal(s)")
    cov = r.coverage_after or {}
    still = f"; still unreached: {', '.join(map(str, r.missed_after))}" if r.missed_after else ""
    print(f"  coverage now {cov.get('pct', '?')}%{still}")
    print(f"  address now {r.address_after}")
    return 0
