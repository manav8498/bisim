"""bisim command line: hash · diff · check · mint · witness · lookup."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

from . import __version__
from .canon import canon, parse_literal
from .core import DiffResult, diff_targets, find_root, hash_target
from .extract import NotFound, Unsupported
from .fmt import clip, describe_obs, fmt_args
from .probes import Probe
from .sandbox import DEFAULT_TIMEOUT, Nondeterministic, SandboxError, observe
from .store import lookup, push
from .witness import add_witness, load_ledger, save_ledger

EXIT_SAME, EXIT_CHANGED, EXIT_WITNESS, EXIT_REFUSED, EXIT_ERROR = 0, 1, 2, 3, 4
MAX_ROWS = 12


class CliError(Exception):
    pass


def _target(s: str) -> tuple[str, str]:
    if ":" not in s:
        raise CliError(f"target must look like path.py:function, got {s!r}")
    path, fn = s.rsplit(":", 1)
    if not fn or not all(part.isidentifier() for part in fn.split(".")):
        raise CliError(f"target must look like path.py:function, path.py:Class or path.py:Class.method, got {s!r}")
    if not os.path.exists(path):
        raise CliError(f"no such file: {path}")
    return path, fn


def _root(args) -> str:
    return str(find_root(args.root) if args.root else find_root())


def _emit_json(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False))


# --- commands ----------------------------------------------------------------

def cmd_hash(args) -> int:
    path, fn = _target(args.target)
    r = hash_target(path, fn, root=_root(args), timeout=args.timeout, count=args.count)
    m = r.manifest
    if args.push:
        push(_root(args), m, r.target.source(), {"name": fn, "path": os.path.abspath(path), "witness_count": m.counts()["witness"]})
    if args.json:
        _emit_json({"address": m.address, "short": m.short(), "counts": m.counts(), "warnings": r.warnings, "manifest": m.to_dict()})
    else:
        c = m.counts()
        print(f"{m.address}")
        print(f"  {r.target.signature_str()}")
        print(f"  probes={len(m.probes)} (type={c['type']} witness={c['witness']} suggested={c['suggested']})  python={m.runtime['python']}  probegen={m.probegen}")
        if m.coverage:
            cov = m.coverage
            missed = f"  missed lines: {', '.join(map(str, cov['missed']))}" if cov["missed"] else ""
            br = cov.get("branches")
            brs = f", branches {br['pct']}% ({br['hit']}/{br['total']})" if br else ""
            print(f"  coverage: lines {cov['pct']}% ({len(cov['hit'])}/{len(cov['executable'])}){brs}{missed}")
        for w in r.warnings:
            print(f"  warning: {w}")
        if args.push:
            print("  pushed to registry")
    return EXIT_SAME


def _diff_to_dict(d: DiffResult) -> dict:
    return {
        "same": d.same, "signature_changed": d.signature_changed, "old_address": d.old_address,
        "new_address": d.new_address, "exit_code": d.exit_code, "warnings": d.warnings,
        "probe_count": d.probe_count, "growth_rounds": d.growth_rounds, "coverage": d.coverage,
        "changes": [dataclasses.asdict(c) for c in d.changes],
    }


def print_diff(d: DiffResult, label_old: str = "old", label_new: str = "new") -> None:
    if d.signature_changed:
        print(f"SIGNATURE CHANGED  {d.warnings[0]}")
        return
    grown = f", grown ×{d.growth_rounds}" if d.growth_rounds else ""
    if d.same:
        print(f"SAME  {d.old_address}  ({d.probe_count} probes agree{grown})")
    else:
        print(f"CHANGED  {label_old}={d.old_address[:17]}…  {label_new}={d.new_address[:17]}…")
        print(f"  changed on {len(d.changes)} of {d.probe_count} inputs ({d.witnessed_changes} witnessed{grown})")
        ordered = sorted(d.changes, key=lambda c: (c.kind != "witness", len(fmt_args(c.args))))
        rows = [(fmt_args(c.args), c.kind, describe_obs(c.old), describe_obs(c.new)) for c in ordered]
        w0 = min(max(len(r[0]) for r in rows), 40)
        w2 = min(max(len(r[2]) for r in rows), 30)
        print(f"  {'input'.ljust(w0)}  {'kind'.ljust(9)}  {label_old.ljust(w2)}  {label_new}")
        for a, k, o, n in rows[:MAX_ROWS]:
            flag = "!" if k == "witness" else " "
            print(f"{flag} {clip(a, w0).ljust(w0)}  {k.ljust(9)}  {clip(o, w2).ljust(w2)}  {clip(n, 40)}")
        if len(rows) > MAX_ROWS:
            print(f"  … and {len(rows) - MAX_ROWS} more (--json lists every input)")
    for w in d.warnings:
        print(f"  warning: {w}")


def cmd_diff(args) -> int:
    op, ofn = _target(args.old)
    np_, nfn = _target(args.new)
    d = diff_targets(op, ofn, np_, nfn, root=_root(args), timeout=args.timeout, count=args.count, grow=not args.no_grow)
    if args.json:
        _emit_json(_diff_to_dict(d))
    else:
        print_diff(d)
    return d.exit_code


def cmd_witness(args) -> int:
    from .target import Target

    path, fn = _target(args.target)
    root = _root(args)
    t = Target.load(path, fn)
    spec = t.spec
    ledger = load_ledger(root, path, fn)
    ledger.function = fn
    ledger.signature = t.signature_str()
    if args.witness_cmd == "list":
        if args.json:
            _emit_json({"function": fn, "signature": ledger.signature, "witnesses": [dataclasses.asdict(w) for w in ledger.witnesses],
                        "suggested": ledger.suggested, "excluded": ledger.excluded})
        else:
            print(f"{fn}{ledger.signature}: {len(ledger.witnesses)} witness(es), {len(ledger.suggested)} suggested, {len(ledger.excluded)} excluded")
            for w in ledger.witnesses:
                note = f"   # {w.note}" if w.note else ""
                print(f"  {fmt_args(w.args)} -> {describe_obs(w.expect)}{note}")
        return EXIT_SAME
    # add
    raw = _witness_args(t, args)
    if args.run:
        expect = observe(path, spec.name if t.kind == "function" else "", [Probe(tuple(raw), "witness")], args.timeout, class_name=t.class_name)[0]
    elif args.raises:
        expect = {"ok": False, "exc": args.raises}
    elif args.expect is not None:
        expect = {"ok": True, "value": canon(parse_literal(args.expect))}
    else:
        raise CliError("give one of --expect <literal>, --raises <ExceptionName>, or --run")
    w = add_witness(ledger, list(raw), expect, args.note or "", "manual")
    p = save_ledger(root, path, fn, ledger)
    if args.json:
        _emit_json({"witness": dataclasses.asdict(w), "ledger": str(p)})
    else:
        print(f"witness recorded: {fmt_args(w.args)} -> {describe_obs(w.expect)}")
        print(f"  ledger: {p}")
    return EXIT_SAME


def _witness_args(t, args) -> tuple:
    """Build the probe args for a witness from --input / --init / --calls according to the target kind."""
    def lst(text, what):
        v = parse_literal(text)
        if not isinstance(v, (list, tuple)):
            raise CliError(f"{what} must be a JSON/Python list, e.g. '[1, 2]'")
        return list(v)

    if t.kind == "function":
        if args.input is None:
            raise CliError("--input is required for a function target")
        raw = lst(args.input, "--input")
        if len(raw) != len(t.spec.params):
            raise CliError(f"--input has {len(raw)} argument(s) but {t.name} takes {len(t.spec.params)}")
        return tuple(raw)
    init = lst(args.init, "--init") if args.init is not None else []
    if len(init) != len(t.spec.init_params):
        raise CliError(f"--init has {len(init)} argument(s) but {t.spec.name}() takes {len(t.spec.init_params)}")
    if t.kind == "method":
        if args.input is None:
            raise CliError("--input (the method's arguments) is required for a method target")
        margs = lst(args.input, "--input")
        if len(margs) != len(t.spec.methods[t.method].params):
            raise CliError(f"--input has {len(margs)} argument(s) but {t.name} takes {len(t.spec.methods[t.method].params)}")
        return (tuple(init), ((t.method, tuple(margs)),))
    if args.calls is None:
        raise CliError("--calls is required for a class target, e.g. '[[\"push\", [1]], [\"pop\", []]]'")
    calls = []
    for c in lst(args.calls, "--calls"):
        if not (isinstance(c, (list, tuple)) and len(c) == 2 and isinstance(c[0], str) and isinstance(c[1], (list, tuple))):
            raise CliError("--calls entries must look like [\"method\", [args]]")
        if c[0] not in t.spec.methods:
            raise CliError(f"{t.spec.name}.{c[0]} is not a supported method")
        calls.append((c[0], tuple(c[1])))
    return (tuple(init), tuple(calls))


def cmd_lookup(args) -> int:
    got = lookup(_root(args), args.address)
    if got is None:
        raise NotFound(f"address not found in registry: {args.address}")
    m = got["manifest"]
    if args.json:
        _emit_json({"address": m.address, "meta": got["meta"], "source": got["source"], "counts": m.counts()})
    else:
        print(f"{m.address}")
        print(f"  {m.function['name']}({', '.join(f'{n}: {t}' for n, t in m.function['params'])}) -> {m.function['returns']}")
        for k, v in sorted(got["meta"].items()):
            print(f"  {k}: {v}")
        print()
        print(got["source"].rstrip())
    return EXIT_SAME


def cmd_check(args) -> int:  # implemented in Task 9
    from .gitcheck import run_check

    return run_check(args)


def cmd_reach(args) -> int:
    from .reachcli import run_reach

    return run_reach(args, _target(args.target))


def cmd_merge_check(args) -> int:
    from .merge import run_merge_check

    return run_merge_check(args)


def cmd_mint(args) -> int:  # implemented in Task 11
    from .mintcli import run_mint

    return run_mint(args)


# --- parser ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bisim", description="Behavior-addressed code: address functions by what they do.")
    p.add_argument("--version", action="version", version=f"bisim {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--root", help="project root (default: nearest .bisim or .git)")
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-probe timeout in seconds")

    h = sub.add_parser("hash", help="compute a function's behavioral address")
    h.add_argument("target", help="path.py:function | path.py:Class | path.py:Class.method")
    h.add_argument("--push", action="store_true", help="store the implementation in the local registry")
    h.add_argument("--count", type=int, default=48, help="number of generated type probes")
    common(h)
    h.set_defaults(func=cmd_hash)

    d = sub.add_parser("diff", help="compare two functions by behavior")
    d.add_argument("old", help="path.py:function")
    d.add_argument("new", help="path.py:function")
    d.add_argument("--count", type=int, default=48, help="initial number of generated probes")
    d.add_argument("--no-grow", action="store_true", help="do not widen the probe set until line coverage plateaus")
    common(d)
    d.set_defaults(func=cmd_diff)

    c = sub.add_parser("check", help="behavioral gate: every changed function in the git worktree vs a base")
    c.add_argument("--base", default="HEAD")
    c.add_argument("--no-grow", action="store_true", help="do not widen probe sets until coverage plateaus")
    common(c)
    c.set_defaults(func=cmd_check)

    rc = sub.add_parser("reach", help="ask a model for inputs that execute lines no probe reaches; keep the ones that verifiably do")
    rc.add_argument("target", help="path.py:function")
    rc.add_argument("--rounds", type=int, default=2)
    rc.add_argument("--max-inputs", type=int, default=6)
    rc.add_argument("--model", default=None)
    rc.add_argument("--client", choices=["auto", "sdk", "claude-code"], default="auto")
    common(rc)
    rc.set_defaults(func=cmd_reach)

    mc = sub.add_parser("merge-check", help="three-way behavioral merge: do two branches change the same function's behavior on the same inputs?")
    mc.add_argument("base", help="merge base ref")
    mc.add_argument("--ours", default="HEAD")
    mc.add_argument("--theirs", required=True)
    mc.add_argument("--count", type=int, default=48, help="number of generated probes per function")
    common(mc)
    mc.set_defaults(func=cmd_merge_check)

    m = sub.add_parser("mint", help="elicit intent: generate candidates, ask about the inputs where they disagree")
    m.add_argument("--intent", required=True)
    m.add_argument("--sig", help='e.g. "def median(xs: list[float]) -> float", or a class stub starting with "class "')
    m.add_argument("--sig-file", help="file containing the signature or class stub")
    m.add_argument("--out", required=True, help="where to write the chosen implementation")
    m.add_argument("--k", type=int, default=6)
    m.add_argument("--max-questions", type=int, default=8)
    m.add_argument("--confirm", type=int, default=3, help="after convergence, confirm up to N once-contested inputs")
    m.add_argument("--model", default=None)
    m.add_argument("--client", choices=["auto", "sdk", "claude-code"], default="auto", help="how to reach the model")
    m.add_argument("--tests-dir", default=None, help="also emit pytest tests from the witnesses")
    common(m)
    m.set_defaults(func=cmd_mint)

    w = sub.add_parser("witness", help="manage the human intent ledger")
    ws = w.add_subparsers(dest="witness_cmd", required=True)
    wa = ws.add_parser("add")
    wa.add_argument("target")
    wa.add_argument("--input", help="argument list, e.g. '[1, 2]' (function or method arguments)")
    wa.add_argument("--init", help="constructor argument list for Class / Class.method targets, e.g. '[3]'")
    wa.add_argument("--calls", help="call sequence for a Class target, e.g. '[[\"push\", [1]], [\"pop\", []]]'")
    wa.add_argument("--expect", help="expected return value as a literal")
    wa.add_argument("--raises", help="expected exception class name")
    wa.add_argument("--run", action="store_true", help="record whatever the current implementation does")
    wa.add_argument("--note", default="")
    common(wa)
    wa.set_defaults(func=cmd_witness)
    wl = ws.add_parser("list")
    wl.add_argument("target")
    common(wl)
    wl.set_defaults(func=cmd_witness)

    lk = sub.add_parser("lookup", help="find a witnessed implementation by address")
    lk.add_argument("address")
    common(lk)
    lk.set_defaults(func=cmd_lookup)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return EXIT_ERROR
    try:
        return args.func(args)
    except (Unsupported, Nondeterministic) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except (CliError, NotFound, SandboxError, FileNotFoundError, ValueError, SyntaxError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
