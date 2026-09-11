"""Offline-reproducible evaluation of ``mint``.

For each task: ask the client for candidates (cached to disk), run ``mint`` against a simulated user
that answers from a hidden reference implementation (the TiCoder evaluation protocol), and record
whether the chosen implementation is behaviorally equivalent to the reference on the final probe set,
versus the naive baseline of taking the model's first candidate.

    python -m bisim.evalbench --tasks examples/bench/tasks.json --cache examples/bench/cache [--client claude-code]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass

from .address import obs_hash
from .core import merge_probes
from .extract import parse_signature
from .mint import Generation, OracleAnswerer, mint
from .probes import generate_type_probes
from .sandbox import SandboxError, observe_source
from .witness import ledger_probes


class CachedClient:
    """Serves generations from ``cache_dir/<task>.json``; falls back to ``inner`` and records."""

    def __init__(self, task: str, cache_dir: str, inner=None):
        self.path = os.path.join(cache_dir, f"{task}.json")
        self.inner = inner
        self.calls = 0

    def generate(self, intent, spec, k, witnesses):
        self.calls += 1
        key = str(len(witnesses))  # round 0 = no witnesses; regeneration rounds keyed by witness count
        cache = json.load(open(self.path)) if os.path.exists(self.path) else {}
        if key in cache:
            g = cache[key]
            return Generation(g["candidates"], g.get("notes", []), g.get("suggested_args", []))
        if self.inner is None:
            raise RuntimeError(f"no cached generation for {self.path} round {key}; pass --client to generate")
        g = self.inner.generate(intent, spec, k, witnesses)
        cache[key] = {"candidates": g.candidates, "notes": g.notes, "suggested_args": g.suggested_args}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        json.dump(cache, open(self.path, "w"), indent=1, ensure_ascii=False)
        return g


@dataclass
class TaskResult:
    name: str
    candidates: int
    classes: int
    questions: int
    mint_correct: bool
    pick1_correct: bool
    probes: int
    seconds: float
    error: str = ""


def _equivalent(src_a: str, src_b: str, spec, probes, timeout=2.0) -> bool:
    oa = observe_source(src_a, spec.name, probes, timeout)
    ob = observe_source(src_b, spec.name, probes, timeout)
    return all(obs_hash(x) == obs_hash(y) for x, y in zip(oa, ob))


def run_task(task: dict, cache_dir: str, inner_client=None, k: int = 6) -> TaskResult:
    t0 = time.time()
    spec = parse_signature(task["sig"])
    client = CachedClient(task["name"], cache_dir, inner_client)
    with tempfile.TemporaryDirectory(prefix="bisim-bench-") as root:
        os.makedirs(os.path.join(root, ".bisim"))
        out = os.path.join(root, f"{task['name']}.py")
        # count initial behavior classes the same way mint does
        gen0 = client.generate(task["intent"], spec, k, [])
        type_probes = generate_type_probes(spec)
        try:
            r = mint(task["intent"], spec, client, OracleAnswerer(task["reference"], spec.name), root, out, k=k, max_questions=12)
        except SandboxError as e:
            return TaskResult(task["name"], len(gen0.candidates), 0, 0, False, False, 0, time.time() - t0, str(e))
        probes = merge_probes(ledger_probes(r.ledger), type_probes)
        # classes among the executable candidates on the initial probe set
        execs = []
        for src in gen0.candidates:
            try:
                execs.append(observe_source(src, spec.name, type_probes))
            except SandboxError:
                continue
        classes = len({tuple(obs_hash(o) for o in obs) for obs in execs})
        mint_ok = _equivalent(r.source, task["reference"], spec, probes)
        first = next((c for c in gen0.candidates if _runs(c, spec, type_probes)), None)
        pick1_ok = bool(first) and _equivalent(first, task["reference"], spec, probes)
        return TaskResult(task["name"], len(execs), classes, r.questions_asked, mint_ok, pick1_ok, len(probes), time.time() - t0)


def _runs(src, spec, probes) -> bool:
    try:
        observe_source(src, spec.name, probes)
        return True
    except SandboxError:
        return False


def summarize(results: list[TaskResult]) -> str:
    ok = [r for r in results if not r.error]
    lines = ["| task | candidates | behavior classes | questions | first-candidate correct | mint correct |", "|---|---|---|---|---|---|"]
    for r in results:
        if r.error:
            lines.append(f"| {r.name} | - | - | - | - | error: {r.error[:60]} |")
        else:
            lines.append(f"| {r.name} | {r.candidates} | {r.classes} | {r.questions} | {'✓' if r.pick1_correct else '✗'} | {'✓' if r.mint_correct else '✗'} |")
    if ok:
        lines.append("")
        lines.append(
            f"**{len(ok)} tasks · first-candidate correct {sum(r.pick1_correct for r in ok)}/{len(ok)} · "
            f"mint correct {sum(r.mint_correct for r in ok)}/{len(ok)} · "
            f"mean classes {statistics.mean(r.classes for r in ok):.1f} · mean questions {statistics.mean(r.questions for r in ok):.1f}**"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="examples/bench/tasks.json")
    ap.add_argument("--cache", default="examples/bench/cache")
    ap.add_argument("--client", choices=["none", "auto", "sdk", "claude-code"], default="none", help="live client to fill cache misses")
    ap.add_argument("--model", default=None)
    ap.add_argument("--only", default=None, help="comma-separated task names")
    ap.add_argument("--json", default=None, help="write results JSON here")
    args = ap.parse_args(argv)
    inner = None
    if args.client != "none":
        from .llm import default_client

        inner = default_client(args.model, prefer=args.client)
    tasks = json.load(open(args.tasks))
    if args.only:
        keep = set(args.only.split(","))
        tasks = [t for t in tasks if t["name"] in keep]
    results = []
    for t in tasks:
        r = run_task(t, args.cache, inner)
        results.append(r)
        print(f"{r.name:12s} cands={r.candidates} classes={r.classes} q={r.questions} pick1={'✓' if r.pick1_correct else '✗'} mint={'✓' if r.mint_correct else '✗'} {r.seconds:.0f}s {r.error}", flush=True)
    print()
    print(summarize(results))
    if args.json:
        json.dump([asdict(r) for r in results], open(args.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
