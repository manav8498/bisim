"""``bisim mint`` command wiring."""
from __future__ import annotations

import os
import sys

from .core import find_root
from .registry import registry_for
from .extract import parse_class_stub, parse_signature
from .fmt import fmt_args, describe_obs
from .mint import ConsoleAnswerer, mint
from .sandbox import SandboxError


def run_mint(args) -> int:
    from .llm import default_client

    root = find_root(args.root) if args.root else find_root(os.path.dirname(os.path.abspath(args.out)))
    sig = open(args.sig_file).read() if getattr(args, "sig_file", None) else args.sig
    if sig is None:
        print("error: give --sig or --sig-file", file=sys.stderr)
        return 4
    spec = parse_class_stub(sig) if sig.lstrip().startswith("class ") else parse_signature(sig)
    try:
        client = default_client(model=args.model, prefer=args.client)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    print(f"minting {spec.signature_str() if hasattr(spec, 'init_params') else spec.name + spec.signature_str()}  intent: {args.intent}")
    print(f"  asking {args.model or 'claude-opus-5'} via {type(client).__name__} for {args.k} deliberately different implementations…")
    try:
        r = mint(args.intent, spec, client, ConsoleAnswerer(), root, args.out, k=args.k,
                 max_questions=args.max_questions, timeout=args.timeout, tests_dir=args.tests_dir, max_confirm=args.confirm,
                 registry=registry_for(root, getattr(args, "registry", None)))
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
    if r.reused:
        print(f"candidates reused from the shared registry: {r.reused}" + ("   (the chosen implementation is one of them)" if r.chosen_from_registry else ""))
    print(f"questions asked: {r.questions_asked}   witnesses: {len(r.ledger.witnesses)}   excluded inputs: {len(r.ledger.excluded)}")
    for w in r.ledger.witnesses:
        print(f"  {fmt_args(w.args)} -> {describe_obs(w.expect)}")
    print(f"wrote {r.out_path}")
    if r.test_path:
        print(f"wrote {r.test_path}")
    print(f"address {r.address}")
    return 0
