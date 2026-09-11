"""``bisim reach``: model-proposed, sandbox-verified inputs for lines no probe reaches."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .canon import canon, dumps
from .core import find_root, hash_function
from .probes import Probe
from .sandbox import DEFAULT_TIMEOUT, Nondeterministic, SandboxError, observe, run_probes_with_coverage
from .witness import load_ledger, save_ledger

REACH_SYSTEM = """You propose concrete inputs that make specific lines of a Python function execute.
Reply with ONE JSON object and nothing else: {"inputs": [[<arg1>, <arg2>, ...], ...]} — each entry is
the full positional argument list for one call, as JSON values matching the parameter types. Propose at
most the number of inputs requested. Prefer the simplest inputs that reach each target line."""


def build_reach_prompt(spec, missed: list[int], max_inputs: int) -> str:
    src_lines = spec.source.splitlines()
    targets = []
    for ln in missed:
        idx = ln - spec.lineno
        text = src_lines[idx].strip() if 0 <= idx < len(src_lines) else "?"
        targets.append(f"  line {ln}: {text}")
    return (
        f"Function (starts at line {spec.lineno}):\n```python\n{spec.source}\n```\n"
        f"Signature: def {spec.name}{spec.signature_str()}\n"
        f"Lines never executed by the current probes:\n" + "\n".join(targets) + "\n"
        f"Propose up to {max_inputs} argument lists that execute these lines."
    )


def parse_inputs(text: str) -> list:
    body = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    body = m.group(1) if m else body[body.find("{"): body.rfind("}") + 1]
    try:
        d = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    return [a for a in (d.get("inputs") if isinstance(d, dict) else []) or [] if isinstance(a, list)]


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


def reach(path: str, fn: str, client, root=None, rounds: int = 2, max_inputs: int = 6, timeout: float = DEFAULT_TIMEOUT) -> ReachResult:
    root = find_root(root or path)
    r = hash_function(path, fn, root=root, timeout=timeout)
    cov = r.manifest.coverage or {"missed": []}
    result = ReachResult(list(cov["missed"]), list(cov["missed"]), coverage_after=cov, address_after=r.manifest.address)
    if not cov["missed"]:
        return result
    spec = r.spec
    ledger = load_ledger(root, path, fn, spec)
    known = {p.id for p in r.probes}
    for _ in range(rounds):
        missed = result.missed_after
        if not missed:
            break
        proposals = parse_inputs(client.complete(REACH_SYSTEM, build_reach_prompt(spec, missed, max_inputs)))
        if not proposals:
            break
        for args in proposals[:max_inputs]:
            if len(args) != len(spec.params):
                result.rejected += 1
                continue
            probe = Probe(tuple(args), "suggested")
            if probe.id in known:
                result.rejected += 1
                continue
            try:
                canon(list(args))
                obs, cov_sets = run_probes_with_coverage(path, fn, [probe], timeout)
                observe(path, fn, [probe], timeout)  # determinism check
            except (SandboxError, Nondeterministic, ValueError):
                result.rejected += 1
                continue
            hit = sorted(set(missed) & cov_sets[0])
            if not hit:
                result.rejected += 1
                continue
            ledger.suggested.append(canon(list(args)))
            known.add(probe.id)
            result.added += 1
            result.inputs.append(list(args))
            result.reached = sorted(set(result.reached) | set(hit))
        save_ledger(root, path, fn, ledger)
        r = hash_function(path, fn, root=root, timeout=timeout)
        result.coverage_after = r.manifest.coverage
        result.missed_after = list(r.manifest.coverage["missed"])
        result.address_after = r.manifest.address
    return result
