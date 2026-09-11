import textwrap

import pytest

from bisim.probes import Probe, dataclass_type
from bisim.sandbox import Nondeterministic, SandboxError, introspect, observe, observe_source, run_probes

SRC = """
import socket, random, dataclasses

@dataclasses.dataclass
class Pt:
    x: int
    y: int

def ok(x: int) -> int: return x * 2
def boom(x: int) -> int: raise ValueError("bad")
def slow(x: int) -> int:
    while True: pass
def loud(x: int) -> int:
    print("hi"); return x
def net(x: int) -> int:
    socket.create_connection(("example.com", 80), timeout=1); return 1
def rnd(x: int) -> int: return random.randint(0, 10**9)
def pt(p: Pt) -> int: return p.x + p.y
def deep(n: int) -> int: return deep(n + 1)
def exit_(n: int) -> int:
    raise SystemExit(3)
def writes(n: int) -> str:
    open("scratch.txt", "w").write("x"); return "wrote"
"""


@pytest.fixture
def mod(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(SRC))
    return str(p)


def test_return_and_exception(mod):
    assert run_probes(mod, "ok", [Probe((3,))]) == [{"ok": True, "value": ["i", "6"]}]
    assert run_probes(mod, "boom", [Probe((3,))]) == [{"ok": False, "exc": "ValueError"}]


def test_timeout(mod):
    assert run_probes(mod, "slow", [Probe((1,))], timeout=0.5) == [{"timeout": True}]


def test_stdout_captured(mod):
    assert run_probes(mod, "loud", [Probe((1,))])[0]["out"] == "hi\n"


def test_network_blocked(mod):
    assert run_probes(mod, "net", [Probe((1,))])[0]["ok"] is False


def test_nondeterministic_refused(mod):
    with pytest.raises(Nondeterministic) as ei:
        observe(mod, "rnd", [Probe((1,))])
    assert "nondeterministic" in str(ei.value)


def test_observe_agrees(mod):
    assert observe(mod, "ok", [Probe((2,)), Probe((5,))]) == [
        {"ok": True, "value": ["i", "4"]},
        {"ok": True, "value": ["i", "10"]},
    ]


def test_observe_source():
    assert observe_source("def f(x: int) -> int:\n    return x + 1\n", "f", [Probe((1,))]) == [
        {"ok": True, "value": ["i", "2"]}
    ]


def test_recursion_and_systemexit_are_observations(mod):
    assert run_probes(mod, "deep", [Probe((1,))]) == [{"ok": False, "exc": "RecursionError"}]
    assert run_probes(mod, "exit_", [Probe((1,))]) == [{"ok": False, "exc": "SystemExit"}]


def test_file_writes_land_in_temp_cwd(mod, tmp_path):
    (o,) = run_probes(mod, "writes", [Probe((1,))])
    assert o["ok"] and o["value"] == ["s", "wrote"]
    assert ["open", "scratch.txt", "w"] in o["effects"] and any(e[0] == "fs" and e[1] == "scratch.txt" for e in o["effects"])
    assert not (tmp_path / "scratch.txt").exists()


def test_dataclass_probe_and_introspect(mod):
    info = introspect(mod, ["Pt", "ok", "Nope"])
    assert info["Pt"] == [("x", "int"), ("y", "int")] and info["ok"] is None and info["Nope"] is None
    P = dataclass_type("Pt", info["Pt"])
    assert run_probes(mod, "pt", [Probe((P(1, 2),))]) == [{"ok": True, "value": ["i", "3"]}]


def test_import_failure_is_error(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("import nonexistent_module_xyz\n")
    with pytest.raises(SandboxError, match="import failed"):
        run_probes(str(p), "f", [Probe((1,))])


def test_missing_function_is_error(mod):
    with pytest.raises(SandboxError, match="not found"):
        run_probes(mod, "nope", [Probe((1,))])
