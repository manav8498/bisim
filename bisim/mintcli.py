"""``bisim mint`` command wiring."""
from __future__ import annotations

import os
import sys

from .core import find_root
from .extract import parse_signature
from .fmt import fmt_args, describe_obs
from .mint import ConsoleAnswerer, mint
from .sandbox import SandboxError


def run_mint(args) -> int:
    from .llm import default_client

    root = find_root(args.root) if args.root else find_root(os.path.dirname(os.path.abspath(args.out)))
    spec = parse_signature(args.sig)
    try:
        client = default_client(model=args.model, prefer=args.client)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    print(f"minting {spec.name}{spec.signature_str()}  intent: {args.intent}")
    print(f"  asking {args.model or 'claude-opus-5'} via {type(client).__name__} for {args.k} deliberately different implementations…")
    try:
        r = mint(args.intent, spec, client, ConsoleAnswerer(), root, args.out, k=args.k,
                 max_questions=args.max_questions, timeout=args.timeout, tests_dir=args.tests_dir)
    except SandboxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    print()
    if r.notes:
        print("ambiguities the model flagged:")
        for n in r.notes:
            print(f"  - {n}")
    print(f"questions asked: {r.questions_asked}   witnesses: {len(r.ledger.witnesses)}   excluded inputs: {len(r.ledger.excluded)}")
    for w in r.ledger.witnesses:
        print(f"  {fmt_args(w.args)} -> {describe_obs(w.expect)}")
    print(f"wrote {r.out_path}")
    if r.test_path:
        print(f"wrote {r.test_path}")
    print(f"address {r.address}")
    return 0
