"""``bisim reach``: model-proposed, sandbox-verified inputs for lines no probe reaches."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .canon import canon, dumps
from .address import coverage_summary
from .core import find_root, hash_target
from .probes import Probe
from .sandbox import DEFAULT_TIMEOUT, Nondeterministic, SandboxError
from .witness import load_ledger, save_ledger

REACH_SYSTEM = """You propose concrete inputs that make specific lines of Python code execute.
Reply with ONE JSON object and nothing else: {"inputs": [ ... ]}. The shape of each entry is given in
the request. Use JSON values matching the parameter types. Propose at most the number of inputs
requested. Prefer the simplest inputs that reach each target line."""

_SHAPES = {
    "function": '[<arg1>, <arg2>, ...]: the full positional argument list for one call',
    "method": '{"init": [<constructor args>], "args": [<method args>]}: one call on a fresh instance',
    "class": '{"init": [<constructor args>], "calls": [["<method>", [<args>]], ...]}: a call sequence on a fresh instance',
}


def build_reach_prompt(target, missed: list[int], max_inputs: int, branches: list[str] = ()) -> str:
    spec = target.spec
    src_lines = spec.source.splitlines()
    targets = []
    for ln in missed:
        idx = ln - spec.lineno
        text = src_lines[idx].strip() if 0 <= idx < len(src_lines) else "?"
        targets.append(f"  line {ln}: {text}")
    head = "Function" if target.kind == "function" else "Class"
    if branches:
        targets += [f"  {b} branch never taken" for b in branches]
    return (
        f"{head} (starts at line {spec.lineno}):\n```python\n{spec.source}\n```\n"
        f"Target: {target.signature_str()}\n"
        f"Lines / branches never executed by the current probes:\n" + "\n".join(targets) + "\n"
        f"Each entry of \"inputs\" must look like: {_SHAPES[target.kind]}\n"
        f"Propose up to {max_inputs} entries that execute these lines."
    )


def to_probe_args(target, entry):
    """Convert one model-proposed entry into probe args for the target kind, or None if malformed."""
    if target.kind == "function":
        if isinstance(entry, list) and target.spec.required_count() <= len(entry) <= len(target.spec.params):
            return tuple(entry)
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("init"), list) or len(entry["init"]) != len(target.spec.init_params):
        return None
    init = tuple(entry["init"])
    if target.kind == "method":
        args = entry.get("args")
        if not isinstance(args, list) or len(args) != len(target.spec.methods[target.method].params):
            return None
        return (init, ((target.method, tuple(args)),))
    calls = entry.get("calls")
    if not isinstance(calls, list) or not calls:
        return None
    out = []
    for c in calls:
        if not (isinstance(c, list) and len(c) == 2 and isinstance(c[0], str) and isinstance(c[1], list)):
            return None
        if c[0] not in target.spec.methods or len(c[1]) != len(target.spec.methods[c[0]].params):
            return None
        out.append((c[0], tuple(c[1])))
    return (init, tuple(out))


def parse_inputs(text: str) -> list:
    body = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    body = m.group(1) if m else body[body.find("{"): body.rfind("}") + 1]
    try:
        d = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    return [a for a in (d.get("inputs") if isinstance(d, dict) else []) or [] if isinstance(a, (list, dict))]


@dataclass
class ReachResult:
    missed_before: list[int]
    missed_after: list[int]
    added: int = 0
    rejected: int = 0
    reached: list[int] = field(default_factory=list)
    inputs: list[list] = field(default_factory=list)
    coverage_after: dict | None = None
    address_after: str = ""


def reach(path: str, name: str, client, root=None, rounds: int = 2, max_inputs: int = 6, timeout: float = DEFAULT_TIMEOUT) -> ReachResult:
    """Works for functions, ``Class.method`` and ``Class`` targets."""
    root = find_root(root or path)
    r = hash_target(path, name, root=root, timeout=timeout)
    cov = r.manifest.coverage or {"missed": []}
    result = ReachResult(list(cov["missed"]), list(cov["missed"]), coverage_after=cov, address_after=r.manifest.address)
    if not cov["missed"] and not cov.get("branches", {}).get("missed"):
        return result
    target = r.target
    ledger = load_ledger(root, path, name)
    ledger.function = name
    ledger.signature = target.signature_str()
    known = {p.id for p in r.probes}
    for _ in range(rounds):
        missed = result.missed_after
        missed_branches = (result.coverage_after or {}).get("branches", {}).get("missed", [])
        if not missed and not missed_branches:
            break
        branches = (result.coverage_after or {}).get("branches", {}).get("missed", [])
        proposals = parse_inputs(client.complete(REACH_SYSTEM, build_reach_prompt(target, missed, max_inputs, branches)))
        if not proposals:
            break
        for entry in proposals[:max_inputs]:
            args = to_probe_args(target, entry)
            if args is None:
                result.rejected += 1
                continue
            probe = Probe(args, "suggested")
            if probe.id in known:
                result.rejected += 1
                continue
            try:
                canon(list(args))
                obs, cov_sets = target.observe([probe], timeout)  # 2× run: determinism check included
            except (SandboxError, Nondeterministic, ValueError):
                result.rejected += 1
                continue
            hit = sorted(set(missed) & set(cov_sets[0]))
            new_branch = False
            if not hit and missed_branches:
                # does it take a branch that was never taken?
                s1 = coverage_summary(target.lines(), [cov_sets[0]], target.decisions()) or {}
                taken_now = set((result.coverage_after or {}).get("branches", {}).get("missed", [])) - set(s1.get("branches", {}).get("missed", []))
                new_branch = bool(taken_now)
            if not hit and not new_branch:
                result.rejected += 1
                continue
            ledger.suggested.append(canon(list(args)))
            known.add(probe.id)
            result.added += 1
            result.inputs.append(entry)
            result.reached = sorted(set(result.reached) | set(hit))
        save_ledger(root, path, name, ledger)
        r = hash_target(path, name, root=root, timeout=timeout)
        result.coverage_after = r.manifest.coverage
        result.missed_after = list(r.manifest.coverage["missed"])
        result.address_after = r.manifest.address
    return result
