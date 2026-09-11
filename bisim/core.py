"""Orchestration: extract → probes (type + ledger) → observe → manifest; and diff."""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

from .address import Manifest, build_manifest, build_manifest_for, coverage_summary, sig_hash
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
    spec: object  # FunctionSpec or ClassSpec
    probes: list[Probe]
    warnings: list[str] = field(default_factory=list)
    target: object = None  # Target


def hash_target(path: str, name: str, root=None, timeout: float = DEFAULT_TIMEOUT, count: int = DEFAULT_COUNT) -> HashResult:
    """Address a function, a ``Class.method`` (one call on a fresh instance) or a ``Class`` (call sequences)."""
    from .target import Target

    root = find_root(root or os.path.dirname(os.path.abspath(path)))
    t = Target.load(path, name)
    resolver = t.resolver()
    probes, warnings = t.probes(root, count, resolver)
    obs, cov = t.observe(probes, timeout)
    m = t.manifest(probes, obs, cov)
    summary = m.coverage
    if any(r.opaque for r in m.probes):
        warnings.append("some observations are opaque (repr-based); the address may be less portable")
    if summary and summary["missed"]:
        warnings.append(
            f"uncovered: {len(probes)} probes never executed line(s) {', '.join(map(str, summary['missed']))} "
            f"of {t.name}. Add a witness that reaches them, run `bisim reach`, or raise --count"
        )
    if summary and summary.get("branches", {}).get("missed"):
        warnings.append(f"branches never taken: {', '.join(summary['branches']['missed'])}")
    return HashResult(m, t.spec, probes, warnings, t)


def hash_function(path: str, fn: str, root=None, timeout: float = DEFAULT_TIMEOUT, count: int = DEFAULT_COUNT) -> HashResult:
    return hash_target(path, fn, root, timeout, count)


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


def diff_targets(old_path: str, old_name: str, new_path: str, new_name: str, root=None, timeout: float = DEFAULT_TIMEOUT,
                 count: int = DEFAULT_COUNT, grow: bool = True, max_count: int = MAX_COUNT) -> DiffResult:
    """Behavioral diff of two targets (functions, ``Class.method``, or ``Class``). With ``grow``, the
    generated probe set is widened (48 → 96 → …) until every executable line of both sides has run
    at least once, or coverage stops improving."""
    from .target import Target

    root = find_root(root or os.path.dirname(os.path.abspath(new_path)))
    to, tn = Target.load(old_path, old_name), Target.load(new_path, new_name)
    if to.kind != tn.kind or to.sig() != tn.sig():
        return DiffResult(False, True, "", "", [], 2, [f"signature changed: {to.signature_str()} -> {tn.signature_str()}"])
    resolver = tn.resolver() or to.resolver()
    rounds = 0
    prev_hit = (-1, -1)
    while True:
        po, wo = to.probes(root, count, resolver)
        pn, wn = tn.probes(root, count, resolver)
        probes = merge_probes(po, pn)
        oo, co = to.observe(probes, timeout)
        on, cn = tn.observe(probes, timeout)
        summ_o, summ_n = coverage_summary(to.lines(), co, to.decisions()), coverage_summary(tn.lines(), cn, tn.decisions())
        hit = tuple((len(s["hit"]) + s.get("branches", {}).get("hit", 0)) if s else 0 for s in (summ_o, summ_n))
        complete = all(s is None or (not s["missed"] and not s.get("branches", {}).get("missed")) for s in (summ_o, summ_n))
        if not grow or complete or hit == prev_hit or count >= max_count:
            break
        prev_hit = hit
        count = min(count * 2, max_count)
        rounds += 1
    mo = build_manifest_for(to.describe(), to.sig(), probes, oo, summ_o)
    mn = build_manifest_for(tn.describe(), tn.sig(), probes, on, summ_n)
    changes = [Change(p.id, p.kind, canon(list(p.args)), a, b) for p, a, b in zip(probes, oo, on) if a != b]
    warnings = wo + wn
    if mo.runtime != mn.runtime:
        warnings.append("runtimes differ")
    for label, t, summ in (("old", to, summ_o), ("new", tn, summ_n)):
        if summ and summ["missed"]:
            warnings.append(f"{label} {os.path.basename(t.path)}:{t.name} line(s) {', '.join(map(str, summ['missed']))} never executed by {len(probes)} probes")
        if summ and summ.get("branches", {}).get("missed"):
            warnings.append(f"{label} {os.path.basename(t.path)}:{t.name} branches never taken: {', '.join(summ['branches']['missed'])}")
    code = 0 if not changes else (2 if any(c.kind == "witness" for c in changes) else 1)
    return DiffResult(not changes, False, mo.address, mn.address, changes, code, warnings, len(probes), rounds,
                      {"old": summ_o, "new": summ_n})


def diff_functions(old_path: str, old_fn: str, new_path: str, new_fn: str, root=None, timeout: float = DEFAULT_TIMEOUT,
                   count: int = DEFAULT_COUNT, grow: bool = True, max_count: int = MAX_COUNT) -> DiffResult:
    return diff_targets(old_path, old_fn, new_path, new_fn, root, timeout, count, grow, max_count)
