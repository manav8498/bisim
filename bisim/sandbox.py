"""Run probes against a function in an isolated child process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from .canon import canon
from .probes import Probe

DEFAULT_TIMEOUT = 2.0


class SandboxError(Exception):
    pass


class CovSet(set):
    """Set of executed lines, plus the (from, to) line arcs that were taken (-1 = return, -2 = entry)."""

    def __init__(self, lines=(), arcs=()):
        super().__init__(lines)
        self.arcs: set[tuple[int, int]] = set(arcs)

    def __or__(self, other):
        out = CovSet(set(self) | set(other), self.arcs | getattr(other, "arcs", set()))
        return out


@dataclass
class Nondeterministic(SandboxError):
    probe: Probe
    first: dict
    second: dict
    reason: str = "nondeterministic"

    def __str__(self) -> str:
        return f"{self.reason} on args={self.probe.args!r}: {self.first} vs {self.second}"


def isolation_mode() -> str:
    """``module`` (default): re-import the target and project-local modules before every probe.
    ``process``: one child process per probe. ``none``: import once (state can leak; only for tests)."""
    mode = os.environ.get("BISIM_ISOLATION", "module").strip().lower()
    return mode if mode in ("module", "process", "none") else "module"


def runner_wrapper() -> list[str]:
    """Optional command prefix for the child process, e.g. ``bwrap --unshare-net ...`` or a
    container runner, set with BISIM_RUNNER_WRAPPER. This is how you get an OS-level sandbox."""
    import shlex

    raw = os.environ.get("BISIM_RUNNER_WRAPPER", "").strip()
    return shlex.split(raw) if raw else []


def _spawn(job: dict, total_timeout: float) -> list[dict]:
    env = dict(os.environ, PYTHONHASHSEED="0")
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = pkg_parent + os.pathsep + env.get("PYTHONPATH", "")
    cmd = runner_wrapper() + [sys.executable, "-c", "import bisim._runner as r; r.main()"]
    try:
        proc = subprocess.run(cmd, input=json.dumps(job), capture_output=True, text=True, env=env, timeout=total_timeout)
    except subprocess.TimeoutExpired:
        raise SandboxError("sandbox exceeded its total time budget")
    except FileNotFoundError as e:
        raise SandboxError(f"cannot start the runner: {e}")
    lines = []
    for raw in proc.stdout.splitlines():
        if raw.strip():
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                raise SandboxError(f"sandbox protocol corrupted: {raw[:200]!r}")
    if lines and "fatal" in lines[0]:
        raise SandboxError(lines[0]["fatal"])
    expected = len(job.get("probes", [])) if job.get("op") == "run" else 1
    if len(lines) < expected:
        why = "killed: memory or total time budget exceeded" if proc.returncode in (-9, 137) else f"rc={proc.returncode}"
        raise SandboxError(f"sandbox crashed after {len(lines)} observation(s) ({why}): {proc.stderr[-2000:]}".rstrip(": "))
    return lines


def _run(module_path: str, func_name: str, probes: list[Probe], timeout: float, coverage: bool, class_name: str | None = None,
         properties: list[str] | None = None) -> tuple[list[dict], list[set[int]]]:
    mode = isolation_mode()
    base = {
        "op": "run",
        "module": os.path.abspath(module_path),
        "func": func_name,
        "class": class_name,
        "properties": list(properties or []),
        "timeout": timeout,
        "coverage": coverage,
        "isolation": mode,
    }
    if mode == "process":
        lines = []
        for p in probes:
            lines += _spawn({**base, "probes": [canon(list(p.args))]}, total_timeout=timeout + 15)
    else:
        lines = _spawn({**base, "probes": [canon(list(p.args)) for p in probes]}, total_timeout=len(probes) * timeout + 15)
    if len(lines) != len(probes):
        raise SandboxError(f"expected {len(probes)} observations, got {len(lines)}")
    cov = []
    for rec in lines:  # coverage is diagnostic: never part of an observation
        hits = set(rec.pop("cov", []))
        arcs = {(a, b) for a, b in rec.pop("arcs", [])}
        cov.append(CovSet(hits, arcs))
    return lines, cov


def run_probes(module_path: str, func_name: str, probes: list[Probe], timeout: float = DEFAULT_TIMEOUT, class_name: str | None = None, properties=None) -> list[dict]:
    """One process, one pass. Returns one observation record per probe."""
    return _run(module_path, func_name, probes, timeout, False, class_name, properties)[0]


def run_probes_with_coverage(module_path: str, func_name: str, probes: list[Probe], timeout: float = DEFAULT_TIMEOUT, class_name: str | None = None, properties=None) -> tuple[list[dict], list[set[int]]]:
    """Like ``run_probes`` but also returns, per probe, the set of module lines executed."""
    return _run(module_path, func_name, probes, timeout, True, class_name, properties)


def observe_cov(module_path: str, func_name: str, probes: list[Probe], timeout: float = DEFAULT_TIMEOUT, class_name: str | None = None, properties=None) -> tuple[list[dict], list[set[int]]]:
    """Two independent processes; any disagreement means the target is nondeterministic.
    Coverage comes from the first run."""
    a, cov = _run(module_path, func_name, probes, timeout, True, class_name, properties)
    b = _run(module_path, func_name, probes[::-1], timeout, False, class_name, properties)[0][::-1]
    _compare_runs(probes, a, b)
    return a, cov


def _compare_runs(probes, a, b):
    """The second run used the reversed order. A mismatch means the result depends on either
    randomness or on state shared between probes; both make the target unaddressable."""
    for p, x, y in zip(probes, a, b):
        if x != y:
            raise Nondeterministic(p, x, y, "nondeterministic or order-dependent (shared state)")


def observe(module_path: str, func_name: str, probes: list[Probe], timeout: float = DEFAULT_TIMEOUT, class_name: str | None = None, properties=None) -> list[dict]:
    """Two independent processes; any disagreement means the target is nondeterministic."""
    a = run_probes(module_path, func_name, probes, timeout, class_name, properties)
    b = run_probes(module_path, func_name, probes[::-1], timeout, class_name, properties)[::-1]
    _compare_runs(probes, a, b)
    return a


def observe_source(source: str, func_name: str, probes: list[Probe], timeout: float = DEFAULT_TIMEOUT, prelude: str = "") -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="bisim-src-") as d:
        path = os.path.join(d, "candidate.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(prelude + "\n" + source + "\n")
        return observe(path, func_name, probes, timeout)


def introspect(module_path: str, names: list[str]) -> dict:
    """For each name: dataclass field list ``[(name, type_str), ...]`` or ``None``."""
    if not names:
        return {}
    job = {"op": "introspect", "module": os.path.abspath(module_path), "names": list(names)}
    lines = _spawn(job, total_timeout=30)
    res = lines[0].get("introspect", {}) if lines else {}
    return {k: ([tuple(f) for f in v] if v else None) for k, v in res.items()}
