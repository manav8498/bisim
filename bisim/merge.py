"""``bisim merge-check``: three-way *behavioral* merge analysis.

Git merges text. This asks a different question: given a base and two branches, did both branches
change the same function's behavior on the same inputs — and differently? That is a conflict even if
the text merges cleanly. Disjoint behavioral changes are reported as independent; identical changes as
convergent.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field

from .address import obs_hash, sig_hash
from .canon import canon
from .core import find_root, merge_probes
from .extract import FunctionSpec, Unsupported, extract_functions
from .fmt import describe_obs, fmt_args
from .gitdiff import NotARepo, ensure_repo, file_at_revision, git
from .probes import generate_type_probes
from .sandbox import Nondeterministic, SandboxError, observe
from .witness import ledger_probes, load_ledger

STATUS_EXIT = {
    "conflict": 2, "delete-modify": 2, "added-both-conflict": 2, "signature": 2,
    "convergent": 0, "independent": 0, "ours-only": 0, "theirs-only": 0, "unchanged": 0,
    "added-both-same": 0, "removed-both": 0, "refused": 3, "error": 4,
}


@dataclass
class Row:
    rel: str
    name: str
    status: str
    detail: str = ""
    conflicts: int = 0
    evidence: list[dict] = field(default_factory=list)


def _changed_files(root, a: str, b: str) -> list[str]:
    out = git(root, "diff", "--name-only", "--diff-filter=AMD", a, b, "--", "*.py")
    return [l.strip() for l in out.splitlines() if l.strip()]


def _specs_at(root, rel: str, rev: str, tmpdir: str) -> dict[str, FunctionSpec]:
    src = file_at_revision(root, rel, rev)
    if src is None:
        return {}
    path = os.path.join(tmpdir, f"{rev.replace('/', '_')}__{rel.replace(os.sep, '__')}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    try:
        return {s.name: s for s in extract_functions(path)}
    except SyntaxError:
        return {}


def _probes(root, rel: str, spec: FunctionSpec, count: int):
    real = os.path.join(root, rel)
    ledger = load_ledger(root, real, spec.name, spec)
    return merge_probes(ledger_probes(ledger), generate_type_probes(spec, count))


def merge_check(root, base: str, ours: str, theirs: str, timeout: float = 2.0, count: int = 48) -> list[Row]:
    root = str(find_root(root))
    ensure_repo(root)
    rels = sorted(set(_changed_files(root, base, ours)) | set(_changed_files(root, base, theirs)))
    rows: list[Row] = []
    tmpdir = tempfile.mkdtemp(prefix="bisim-merge-")
    for rel in rels:
        B, O, T = (_specs_at(root, rel, r, tmpdir) for r in (base, ours, theirs))
        for name in sorted(set(B) | set(O) | set(T)):
            b, o, t = B.get(name), O.get(name), T.get(name)
            try:
                rows.append(_classify(root, rel, name, b, o, t, timeout, count))
            except (Unsupported, Nondeterministic) as e:
                rows.append(Row(rel, name, "refused", str(e)))
            except SandboxError as e:
                rows.append(Row(rel, name, "error", str(e)))
    return [r for r in rows if r.status != "unchanged"]


def _obs(spec: FunctionSpec, probes, timeout):
    return observe(spec.module_path, spec.name, probes, timeout)


def _classify(root, rel, name, b, o, t, timeout, count) -> Row:
    if o is None and t is None:
        return Row(rel, name, "removed-both")
    if b is None:  # added on one or both sides
        if o is None:
            return Row(rel, name, "theirs-only", "added")
        if t is None:
            return Row(rel, name, "ours-only", "added")
        if sig_hash(o) != sig_hash(t):
            return Row(rel, name, "signature", f"added with different signatures: {o.signature_str()} vs {t.signature_str()}")
        probes = merge_probes(_probes(root, rel, o, count), _probes(root, rel, t, count))
        oo, ot = _obs(o, probes, timeout), _obs(t, probes, timeout)
        diffs = [(p, x, y) for p, x, y in zip(probes, oo, ot) if x != y]
        if not diffs:
            return Row(rel, name, "added-both-same")
        return Row(rel, name, "added-both-conflict", f"{len(diffs)}/{len(probes)} inputs differ", len(diffs),
                   [{"input": fmt_args(canon(list(p.args))), "ours": describe_obs(x), "theirs": describe_obs(y)} for p, x, y in diffs[:5]])
    # base exists
    sides = {"ours": o, "theirs": t}
    for label, s in sides.items():
        if s is not None and sig_hash(s) != sig_hash(b):
            return Row(rel, name, "signature", f"{label} changed the signature: {b.signature_str()} -> {s.signature_str()}")
    probes = _probes(root, rel, b, count)
    for s in (o, t):
        if s is not None:
            probes = merge_probes(probes, _probes(root, rel, s, count))
    ob = _obs(b, probes, timeout)
    changed = {}
    obs_side = {}
    for label, s in sides.items():
        if s is None:
            continue
        os_ = _obs(s, probes, timeout)
        obs_side[label] = os_
        changed[label] = {p.id for p, x, y in zip(probes, ob, os_) if x != y}
    if o is None or t is None:  # deleted on one side
        deleted, kept = ("ours", "theirs") if o is None else ("theirs", "ours")
        if changed[kept]:
            return Row(rel, name, "delete-modify", f"deleted in {deleted}, behavior changed in {kept} on {len(changed[kept])} inputs", len(changed[kept]))
        return Row(rel, name, f"{deleted}-only", "deleted")
    co, ct = changed["ours"], changed["theirs"]
    if not co and not ct:
        return Row(rel, name, "unchanged")
    if not ct:
        return Row(rel, name, "ours-only", f"behavior changed on {len(co)} inputs")
    if not co:
        return Row(rel, name, "theirs-only", f"behavior changed on {len(ct)} inputs")
    overlap = co & ct
    conflicts = []
    for p, x, y, z in zip(probes, ob, obs_side["ours"], obs_side["theirs"]):
        if p.id in overlap and obs_hash(y) != obs_hash(z):
            conflicts.append({"input": fmt_args(canon(list(p.args))), "base": describe_obs(x), "ours": describe_obs(y), "theirs": describe_obs(z), "kind": p.kind})
    if conflicts:
        conflicts.sort(key=lambda e: (e["kind"] != "witness", len(e["input"])))
        return Row(rel, name, "conflict", f"both changed {len(overlap)} shared inputs; {len(conflicts)} disagree", len(conflicts), conflicts[:5])
    if not overlap:
        return Row(rel, name, "independent", f"ours changed {len(co)} inputs, theirs {len(ct)}, none shared")
    # all overlapping inputs changed identically
    if co == ct and all(obs_hash(y) == obs_hash(z) for p, y, z in zip(probes, obs_side["ours"], obs_side["theirs"]) if p.id in co):
        return Row(rel, name, "convergent", f"both made the same change on {len(co)} inputs")
    return Row(rel, name, "independent", f"{len(overlap)} shared inputs changed identically; {len(co ^ ct)} changed on one side only")


def run_merge_check(args) -> int:
    root = find_root(args.root) if args.root else find_root()
    try:
        rows = merge_check(root, args.base, args.ours, args.theirs, args.timeout, args.count)
    except NotARepo as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    code = max((STATUS_EXIT[r.status] for r in rows), default=0)
    if args.json:
        print(json.dumps({"base": args.base, "ours": args.ours, "theirs": args.theirs, "exit_code": code,
                          "results": [asdict(r) for r in rows]}, indent=1, sort_keys=True, ensure_ascii=False))
        return code
    if not rows:
        print(f"nothing to check: no Python functions changed between {args.base} and {args.ours}/{args.theirs}")
        return 0
    w = max(len(f"{r.rel}:{r.name}") for r in rows)
    for r in rows:
        tag = r.status.upper() if STATUS_EXIT[r.status] >= 2 else r.status
        print(f"{f'{r.rel}:{r.name}'.ljust(w)}  {tag.ljust(20)} {r.detail}".rstrip())
        for e in r.evidence:
            flag = "!" if e.get("kind") == "witness" else " "
            base = f"base={e['base']}  " if "base" in e else ""
            print(f"  {flag} {e['input']}: {base}ours={e['ours']}  theirs={e['theirs']}")
    summary = {}
    for r in rows:
        summary[r.status] = summary.get(r.status, 0) + 1
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(summary.items())), f"(exit {code})")
    return code
