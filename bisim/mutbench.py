"""Mutation benchmark for the address.

For every function in a corpus, generate mutants with simple source transformations. An independent
fuzzing oracle (a different seed stream, many more inputs) decides whether a mutant really changes
behavior. Then measure how many of the changed mutants the standard 48-probe address catches, and how
many are caught after growth. Equivalent mutants must never be reported as changed.

    python -m bisim.mutbench [--corpus DIR ...] [--oracle-inputs 2000] [--json out.json]
"""
from __future__ import annotations

import argparse
import ast
import copy
import glob
import json
import os
import random
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass

from .address import obs_hash
from .extract import extract_function
from .probes import Probe, STANDARD_COUNT, generate_type_probes, sig_hash, strategy_for
from .sandbox import Nondeterministic, SandboxError, run_probes
from .target import Target


# --- mutation operators ------------------------------------------------------

_CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_ARITH_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add, ast.FloorDiv: ast.Div, ast.Mod: ast.FloorDiv}


def _sites(tree):
    """Yield (description, mutate_fn) for every applicable mutation site in the function."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in _CMP_SWAP:
                    yield (f"compare {type(op).__name__}->{_CMP_SWAP[type(op)].__name__} L{node.lineno}",
                           lambda n=node, i=i, op=op: n.ops.__setitem__(i, _CMP_SWAP[type(op)]()))
        elif isinstance(node, ast.BinOp) and type(node.op) in _ARITH_SWAP:
            yield (f"arith {type(node.op).__name__}->{_ARITH_SWAP[type(node.op)].__name__} L{node.lineno}",
                   lambda n=node: setattr(n, "op", _ARITH_SWAP[type(n.op)]()))
        elif isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            yield (f"const {node.value}->{node.value + 1} L{node.lineno}", lambda n=node: setattr(n, "value", n.value + 1))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            yield (f"bool {node.value}->{not node.value} L{node.lineno}", lambda n=node: setattr(n, "value", not n.value))
        elif isinstance(node, ast.If):
            yield (f"negate-if L{node.lineno}", lambda n=node: setattr(n, "test", ast.UnaryOp(op=ast.Not(), operand=n.test)))
        elif isinstance(node, ast.BoolOp):
            yield (f"boolop {type(node.op).__name__} L{node.lineno}",
                   lambda n=node: setattr(n, "op", ast.Or() if isinstance(n.op, ast.And) else ast.And()))
        elif isinstance(node, (ast.FunctionDef, ast.If, ast.For, ast.While)) and len(node.body) > 1:
            for i in range(len(node.body)):
                yield (f"drop-stmt L{node.body[i].lineno}", lambda n=node, i=i: n.body.pop(i))


def mutants(source: str, func: str, limit: int | None = None) -> list[tuple[str, str]]:
    """(description, mutated source) for one function. Only the target function is mutated."""
    module = ast.parse(source)
    out = []
    for fn in [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == func]:
        n_sites = sum(1 for _ in _sites(fn))
        for idx in range(n_sites):
            m = copy.deepcopy(module)
            target = next(n for n in m.body if isinstance(n, ast.FunctionDef) and n.name == func)
            desc, mutate = list(_sites(target))[idx]
            try:
                mutate()
                src = ast.unparse(ast.fix_missing_locations(m))
                compile(src, "<mutant>", "exec")
            except Exception:  # noqa: BLE001
                continue
            if src != ast.unparse(module):
                out.append((desc, src))
    if limit and len(out) > limit:
        rng = random.Random(0)
        out = rng.sample(out, limit)
    return out


# --- oracle --------------------------------------------------------------------

def oracle_probes(spec, n: int, salt: str = "oracle") -> list[Probe]:
    """Inputs from a different seed stream than the standard probes, so the oracle is independent."""
    strategies = [strategy_for(t) for _, t in spec.params]
    rng = random.Random(int((sig_hash(spec) + salt).encode().hex()[:16], 16))
    out, seen = [], set()
    std = {p.id for p in generate_type_probes(spec, STANDARD_COUNT)}
    guard = 0
    while len(out) < n and guard < n * 10:
        guard += 1
        args = tuple(rng.choice(s.boundaries) if rng.random() < 0.3 else s.draw(rng) for s in strategies)
        if spec.required_count() < len(strategies) and rng.random() < 0.3:
            args = args[: rng.randint(spec.required_count(), len(strategies) - 1)]
        p = Probe(args, "type")
        if p.id in seen or p.id in std:
            continue
        seen.add(p.id)
        out.append(p)
    return out


@dataclass
class MutantResult:
    corpus_file: str
    func: str
    mutation: str
    oracle_differs: bool | None  # None = mutant could not be run (refused)
    standard_differs: bool | None
    grown_differs: bool | None
    grown_probes: int = 0


def _observe_file(path: str, func: str, probes: list[Probe], timeout: float) -> list[dict] | None:
    try:
        return run_probes(path, func, probes, timeout)
    except (SandboxError, Nondeterministic):
        return None


CHUNK = 250


def _oracle_differs(base_path, mut_path, func, orc, base_orc_chunks, timeout) -> bool | None:
    """Run the oracle inputs in chunks and stop at the first difference. None if the mutant cannot run."""
    for start in range(0, len(orc), CHUNK):
        chunk = orc[start:start + CHUNK]
        m = _observe_file(mut_path, func, chunk, timeout)
        if m is None:
            return None
        base = base_orc_chunks[start // CHUNK]
        if any(obs_hash(a) != obs_hash(b) for a, b in zip(base, m)):
            return True
    return False


def run_corpus(files: list[str], oracle_n: int = 2000, per_func: int | None = None, timeout: float = 0.5, log=print) -> list[MutantResult]:
    from .core import diff_targets

    results: list[MutantResult] = []
    for f in files:
        try:
            names = [n.name for n in ast.parse(open(f).read()).body if isinstance(n, ast.FunctionDef)]
        except SyntaxError:
            continue
        for func in names:
            try:
                spec = extract_function(f, func)
            except Exception:  # noqa: BLE001
                continue
            source = open(f).read()
            std = generate_type_probes(spec, STANDARD_COUNT)
            orc = oracle_probes(spec, oracle_n)
            base_std = _observe_file(f, func, std, timeout)
            if base_std is None:
                continue
            base_orc_chunks = []
            ok = True
            for start in range(0, len(orc), CHUNK):
                b = _observe_file(f, func, orc[start:start + CHUNK], timeout)
                if b is None:
                    ok = False
                    break
                base_orc_chunks.append(b)
            if not ok:
                continue
            t0 = time.time()
            ms = mutants(source, func, per_func)
            with tempfile.TemporaryDirectory(prefix="bisim-mut-") as d:
                for i, (desc, src) in enumerate(ms):
                    mp = os.path.join(d, f"m{i}.py")
                    with open(mp, "w") as fh:
                        fh.write(src)
                    m_std = _observe_file(mp, func, std, timeout)
                    if m_std is None:
                        results.append(MutantResult(f, func, desc, None, None, None))
                        continue
                    std_diff = any(obs_hash(a) != obs_hash(b) for a, b in zip(base_std, m_std))
                    if std_diff:
                        # the 48 standard inputs already prove the mutant differs; no oracle needed
                        results.append(MutantResult(f, func, desc, True, True, True, STANDARD_COUNT))
                        continue
                    orc_diff = _oracle_differs(f, mp, func, orc, base_orc_chunks, timeout)
                    if orc_diff is None:
                        results.append(MutantResult(f, func, desc, None, None, None))
                        continue
                    grown_diff, grown_n = False, STANDARD_COUNT
                    if orc_diff:
                        try:
                            dres = diff_targets(f, func, mp, func, root=d, timeout=timeout, grow=True)
                            grown_diff, grown_n = (not dres.same), dres.probe_count
                        except (SandboxError, Nondeterministic):
                            pass
                    results.append(MutantResult(f, func, desc, orc_diff, False, grown_diff, grown_n))
            log(f"{os.path.basename(f)}:{func}: {len(ms)} mutants in {time.time() - t0:.0f}s")
    return results


def summarize(results: list[MutantResult]) -> str:
    ran = [r for r in results if r.oracle_differs is not None]
    changed = [r for r in ran if r.oracle_differs]
    equivalent = [r for r in ran if not r.oracle_differs]
    caught_std = sum(1 for r in changed if r.standard_differs)
    caught_grown = sum(1 for r in changed if r.grown_differs)
    false_pos = sum(1 for r in equivalent if r.standard_differs)
    lines = [
        f"mutants generated: {len(results)}   runnable: {len(ran)}   refused (nondeterministic or crashing): {len(results) - len(ran)}",
        f"oracle says behavior changed: {len(changed)}   oracle finds no difference in {len(equivalent)} (equivalent as far as the oracle can tell)",
        f"caught by the 48 standard probes: {caught_std}/{len(changed)} ({100.0 * caught_std / max(1, len(changed)):.1f}%)",
        f"caught after growth (up to 480 probes): {caught_grown}/{len(changed)} ({100.0 * caught_grown / max(1, len(changed)):.1f}%)",
        f"reported as changed although the oracle saw no difference: {false_pos} (these are real differences the oracle missed, or zero)",
    ]
    missed = [r for r in changed if not r.grown_differs]
    if missed:
        lines.append("missed after growth (first 10):")
        for r in missed[:10]:
            lines.append(f"  {os.path.basename(r.corpus_file)}:{r.func}  {r.mutation}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", nargs="*", default=None, help="files or globs; default: fixture a.py files, bench references, examples")
    ap.add_argument("--oracle-inputs", type=int, default=2000)
    ap.add_argument("--per-func", type=int, default=None, help="cap mutants per function")
    ap.add_argument("--json", default=None)
    ap.add_argument("--timeout", type=float, default=0.5, help="per-input timeout in seconds")
    args = ap.parse_args(argv)
    if args.corpus:
        files = sorted({f for g in args.corpus for f in glob.glob(g)})
    else:
        files = sorted(glob.glob("tests/fixtures/*/*/a.py")) + sorted(glob.glob("examples/median_v1.py"))
        files = [f for f in files if "/class_" not in f]
        # bench references, written to a temp dir
        d = tempfile.mkdtemp(prefix="bisim-refs-")
        for tf in ("examples/bench/tasks.json", "examples/bench/heldout.json"):
            if os.path.exists(tf):
                for t in json.load(open(tf)):
                    p = os.path.join(d, f"{t['name']}.py")
                    open(p, "w").write(t["reference"])
                    files.append(p)
    results = run_corpus(files, args.oracle_inputs, args.per_func, args.timeout)
    print()
    print(summarize(results))
    if args.json:
        json.dump([asdict(r) for r in results], open(args.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
