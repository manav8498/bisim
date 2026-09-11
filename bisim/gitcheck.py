"""``bisim check``: the behavioral gate over a git worktree."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field

from .core import diff_functions, find_root, hash_function
from .extract import Unsupported
from .fmt import describe_obs, fmt_args
from .gitdiff import NotARepo, function_pairs
from .sandbox import Nondeterministic, SandboxError

EXIT_FOR_STATUS = {"same": 0, "changed": 1, "witness-violation": 2, "signature-changed": 2, "added": 0, "removed": 0, "refused": 3, "error": 4}


@dataclass
class Row:
    rel: str
    name: str
    status: str
    detail: str = ""
    old_address: str = ""
    new_address: str = ""
    changes: int = 0
    witnessed_changes: int = 0
    evidence: list[dict] = field(default_factory=list)


def check(root, base: str = "HEAD", timeout: float = 2.0) -> list[Row]:
    root = find_root(root)
    rows: list[Row] = []
    for pr in function_pairs(root, base):
        try:
            if pr.old_path and pr.new_path:
                d = diff_functions(pr.old_path, pr.name, pr.new_path, pr.name, root=root, timeout=timeout)
                if d.signature_changed:
                    rows.append(Row(pr.rel, pr.name, "signature-changed", d.warnings[0]))
                elif d.same:
                    rows.append(Row(pr.rel, pr.name, "same", "", d.old_address, d.new_address))
                else:
                    status = "witness-violation" if d.witnessed_changes else "changed"
                    ev = [
                        {"input": fmt_args(c.args), "kind": c.kind, "old": describe_obs(c.old), "new": describe_obs(c.new)}
                        for c in sorted(d.changes, key=lambda c: (c.kind != "witness", len(fmt_args(c.args))))[:5]
                    ]
                    rows.append(Row(pr.rel, pr.name, status, f"{len(d.changes)}/{d.probe_count} inputs", d.old_address, d.new_address,
                                    len(d.changes), d.witnessed_changes, ev))
            elif pr.new_path:
                r = hash_function(pr.new_path, pr.name, root=root, timeout=timeout)
                rows.append(Row(pr.rel, pr.name, "added", "", "", r.manifest.address))
            else:
                rows.append(Row(pr.rel, pr.name, "removed"))
        except (Unsupported, Nondeterministic) as e:
            rows.append(Row(pr.rel, pr.name, "refused", str(e)))
        except SandboxError as e:
            rows.append(Row(pr.rel, pr.name, "error", str(e)))
    return rows


def run_check(args) -> int:
    root = find_root(args.root) if args.root else find_root()
    try:
        rows = check(root, args.base, args.timeout)
    except NotARepo as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    code = max((EXIT_FOR_STATUS[r.status] for r in rows), default=0)
    if args.json:
        print(json.dumps({"base": args.base, "exit_code": code, "results": [asdict(r) for r in rows]}, indent=1, sort_keys=True, ensure_ascii=False))
        return code
    if not rows:
        print("nothing to check: no changed Python functions vs", args.base)
        return 0
    w = max(len(f"{r.rel}:{r.name}") for r in rows)
    for r in rows:
        tag = {"same": "same", "changed": "changed", "witness-violation": "WITNESS-VIOLATION", "signature-changed": "SIGNATURE-CHANGED",
               "added": "added", "removed": "removed", "refused": "refused", "error": "error"}[r.status]
        addr = ""
        if r.status == "same":
            addr = r.old_address[:17] + "…"
        elif r.status in ("changed", "witness-violation"):
            addr = f"{r.old_address[:17]}… -> {r.new_address[:17]}…"
        elif r.status == "added":
            addr = r.new_address[:17] + "…"
        print(f"{f'{r.rel}:{r.name}'.ljust(w)}  {tag.ljust(18)} {r.detail}  {addr}".rstrip())
        for e in r.evidence:
            flag = "!" if e["kind"] == "witness" else " "
            print(f"  {flag} {e['input']}: {e['old']} -> {e['new']}")
    summary = {}
    for r in rows:
        summary[r.status] = summary.get(r.status, 0) + 1
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(summary.items())), f"(exit {code})")
    return code
