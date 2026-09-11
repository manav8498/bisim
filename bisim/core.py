"""Orchestration: extract → probes (type + ledger) → observe → manifest; and diff."""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

from .address import Manifest, build_manifest, coverage_summary, sig_hash
from .canon import canon
from .extract import FunctionSpec, Unsupported, extract_function
from .probes import DEFAULT_COUNT, Probe, dataclass_type, generate_type_probes
from .sandbox import DEFAULT_TIMEOUT, introspect, observe, observe_cov
from .witness import ledger_probes, load_ledger

KIND_RANK = {"type": 0, "suggested": 1, "witness": 2}


def find_root(start=None) -> Path:
    p = Path(start or os.getcwd()).resolve()
    if p.is_file():
        p = p.parent
    for d in [p, *p.parents]:
        if (d / ".bisim").is_dir() or (d / ".git").is_dir():
            return d
    return p


def _names_in_types(spec: FunctionSpec) -> list[str]:
    names = set()
    for _, t in spec.params:
        try:
            tree = ast.parse(t, mode="eval")
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                names.add(n.id)
    return sorted(names)


def resolver_for(spec: FunctionSpec) -> dict | None:
    """Dataclass field maps for names used in the signature, discovered in the sandbox."""
    if not spec.module_path or not os.path.exists(spec.module_path):
        return None
    info = introspect(spec.module_path, _names_in_types(spec))
    res = {k: v for k, v in info.items() if v}
    return res or None


def _class_resolver(resolver):
    if not resolver:
        return None
    return lambda q: dataclass_type(q, resolver[q.split(".")[-1]])


def probes_for(spec: FunctionSpec, root, count: int = DEFAULT_COUNT, resolver=None) -> tuple[list[Probe], list[str]]:
    """Ledger probes (witness > suggested) first, then generated type probes; deduped by id."""
    warnings: list[str] = []
    lp: list[Probe] = []
    if spec.module_path:
        ledger = load_ledger(root, spec.module_path, spec.name, spec)
        lp = ledger_probes(ledger, _class_resolver(resolver))
    try:
        tp = generate_type_probes(spec, count, resolver)
    except Unsupported as e:
        if not lp:
            raise
        warnings.append(f"witness-only: {e}")
        tp = []
    seen = {p.id for p in lp}
    out = list(lp)
    for p in tp:
        if p.id not in seen:
            seen.add(p.id)
            out.append(p)
    return out, warnings


@dataclass
class HashResult:
    manifest: Manifest
    spec: FunctionSpec
    probes: list[Probe]
    warnings: list[str] = field(default_factory=list)


def hash_function(path: str, fn: str, root=None, timeout: float = DEFAULT_TIMEOUT, count: int = DEFAULT_COUNT) -> HashResult:
    root = find_root(root or os.path.dirname(os.path.abspath(path)))
    spec = extract_function(path, fn)
    resolver = resolver_for(spec)
    probes, warnings = probes_for(spec, root, count, resolver)
    obs, cov = observe_cov(path, fn, probes, timeout)
    summary = coverage_summary(spec, cov)
    m = build_manifest(spec, probes, obs, summary)
    if any(r.opaque for r in m.probes):
        warnings.append("some observations are opaque (repr-based); the address may be less portable")
    if summary and summary["missed"]:
        warnings.append(
            f"uncovered: {len(probes)} probes never executed line(s) {', '.join(map(str, summary['missed']))} "
            f"of {fn} — add a witness that reaches them, or raise --count"
        )
    return HashResult(m, spec, probes, warnings)


@dataclass
class Change:
    probe_id: str
    kind: str
    args: list  # canonical
    old: dict
    new: dict


@dataclass
class DiffResult:
    same: bool
    signature_changed: bool
    old_address: str
    new_address: str
    changes: list[Change]
    exit_code: int
    warnings: list[str] = field(default_factory=list)
    probe_count: int = 0
    growth_rounds: int = 0
    coverage: dict | None = None  # {"old": summary, "new": summary}

    @property
    def witnessed_changes(self) -> int:
        return sum(1 for c in self.changes if c.kind == "witness")


def merge_probes(*lists: list[Probe]) -> list[Probe]:
    """Union by id; the strongest kind (witness > suggested > type) wins."""
    index: dict[str, int] = {}
    out: list[Probe] = []
    for lst in lists:
        for p in lst:
            i = index.get(p.id)
            if i is None:
                index[p.id] = len(out)
                out.append(p)
            elif KIND_RANK[p.kind] > KIND_RANK[out[i].kind]:
                out[i] = p
    return out


MAX_COUNT = 480


def diff_functions(old_path: str, old_fn: str, new_path: str, new_fn: str, root=None, timeout: float = DEFAULT_TIMEOUT,
                   count: int = DEFAULT_COUNT, grow: bool = True, max_count: int = MAX_COUNT) -> DiffResult:
    """Behavioral diff. With ``grow``, the generated probe set is widened (48 → 96 → …) until every
    executable line of both functions has run at least once, or coverage stops improving."""
    root = find_root(root or os.path.dirname(os.path.abspath(new_path)))
    so, sn = extract_function(old_path, old_fn), extract_function(new_path, new_fn)
    if sig_hash(so) != sig_hash(sn):
        return DiffResult(False, True, "", "", [], 2, [f"signature changed: {so.signature_str()} -> {sn.signature_str()}"])
    resolver = resolver_for(sn) or resolver_for(so)
    rounds = 0
    prev_hit = (-1, -1)
    while True:
        po, wo = probes_for(so, root, count, resolver)
        pn, wn = probes_for(sn, root, count, resolver)
        probes = merge_probes(po, pn)
        oo, co = observe_cov(old_path, old_fn, probes, timeout)
        on, cn = observe_cov(new_path, new_fn, probes, timeout)
        summ_o, summ_n = coverage_summary(so, co), coverage_summary(sn, cn)
        hit = (len(summ_o["hit"]) if summ_o else 0, len(summ_n["hit"]) if summ_n else 0)
        complete = all(s is None or not s["missed"] for s in (summ_o, summ_n))
        if not grow or complete or hit == prev_hit or count >= max_count:
            break
        prev_hit = hit
        count = min(count * 2, max_count)
        rounds += 1
    mo = build_manifest(so, probes, oo, summ_o)
    mn = build_manifest(sn, probes, on, summ_n)
    changes = [Change(p.id, p.kind, canon(list(p.args)), a, b) for p, a, b in zip(probes, oo, on) if a != b]
    warnings = wo + wn
    if mo.runtime != mn.runtime:
        warnings.append("runtimes differ")
    for label, path, fn, summ in (("old", old_path, old_fn, summ_o), ("new", new_path, new_fn, summ_n)):
        if summ and summ["missed"]:
            warnings.append(f"{label} {os.path.basename(path)}:{fn} line(s) {', '.join(map(str, summ['missed']))} never executed by {len(probes)} probes")
    code = 0 if not changes else (2 if any(c.kind == "witness" for c in changes) else 1)
    return DiffResult(not changes, False, mo.address, mn.address, changes, code, warnings, len(probes), rounds,
                      {"old": summ_o, "new": summ_n})
