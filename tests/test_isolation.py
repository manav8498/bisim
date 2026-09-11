import textwrap

import pytest

from bisim.probes import Probe
from bisim.sandbox import Nondeterministic, observe, run_probes

GLOBAL_STATE = """
counter = 0

def f(x: int) -> int:
    global counter
    counter += 1
    return counter
"""

IMPORT_EFFECTS = """
import os, socket
os.environ.get("BISIM_IMPORT_TIME_VAR")
try:
    socket.create_connection(("example.com", 80), timeout=1)
except Exception:
    pass
open("import_time.txt", "w").write("x")

def f(x: int) -> int:
    return x
"""

CACHE_IN_HELPER = """
from helper import cached_len

def f(s: str) -> int:
    return cached_len(s)
"""
HELPER = """
_seen = []

def cached_len(s: str) -> int:
    _seen.append(s)
    return len(_seen)
"""


def w(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return str(p)


def test_module_globals_reset_between_probes(tmp_path):
    m = w(tmp_path, "g.py", GLOBAL_STATE)
    obs = run_probes(m, "f", [Probe((1,)), Probe((2,)), Probe((3,))])
    assert [o["value"] for o in obs] == [["i", "1"]] * 3  # each probe starts from a fresh module
    obs2 = run_probes(m, "f", [Probe((1,)), Probe((2,))])
    assert obs2 == obs[:2]  # adding or removing probes never changes another probe's result


def test_project_local_helper_state_reset_between_probes(tmp_path):
    w(tmp_path, "helper.py", HELPER)
    m = w(tmp_path, "m.py", CACHE_IN_HELPER)
    obs = run_probes(m, "f", [Probe(("a",)), Probe(("b",))])
    assert [o["value"] for o in obs] == [["i", "1"], ["i", "1"]]


def test_import_time_effects_are_blocked_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("BISIM_IMPORT_TIME_VAR", "1")
    m = w(tmp_path, "imp.py", IMPORT_EFFECTS)
    (o,) = run_probes(m, "f", [Probe((5,))])
    assert o["ok"] and o["value"] == ["i", "5"]
    kinds = {e[0] for e in o["effects"]}
    assert {"env", "net", "open", "fs"} <= kinds
    assert not (tmp_path / "import_time.txt").exists()  # the write landed in the scratch dir, not the project


def test_order_dependence_is_refused_even_if_isolation_leaks(tmp_path, monkeypatch):
    # simulate leaked state by telling the runner not to reset modules; the reversed second run must catch it
    monkeypatch.setenv("BISIM_ISOLATION", "none")
    m = w(tmp_path, "g.py", GLOBAL_STATE)
    with pytest.raises(Nondeterministic) as ei:
        observe(m, "f", [Probe((1,)), Probe((2,))])
    assert "order" in str(ei.value)


def test_process_isolation_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("BISIM_ISOLATION", "process")
    m = w(tmp_path, "g.py", GLOBAL_STATE)
    obs = observe(m, "f", [Probe((1,)), Probe((2,))])
    assert [o["value"] for o in obs] == [["i", "1"], ["i", "1"]]


def test_runner_wrapper_is_prepended(tmp_path, monkeypatch):
    m = w(tmp_path, "g.py", "def f(x: int) -> int:\n    return x\n")
    monkeypatch.setenv("BISIM_RUNNER_WRAPPER", "env BISIM_WRAPPED=1")
    (o,) = run_probes(m, "f", [Probe((2,))])
    assert o["value"] == ["i", "2"]
    monkeypatch.setenv("BISIM_RUNNER_WRAPPER", "/definitely/not/a/program")
    from bisim.sandbox import SandboxError

    with pytest.raises(SandboxError):
        run_probes(m, "f", [Probe((2,))])
