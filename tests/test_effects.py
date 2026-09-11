import textwrap

from bisim.core import diff_targets, hash_target
from bisim.fmt import describe_obs
from bisim.probes import Probe
from bisim.sandbox import observe, run_probes

SRC = '''
import os, socket, subprocess, pathlib

def writer(n: int) -> int:
    k = min(max(n, 0), 100)
    with open("out.txt", "w") as fh:
        fh.write("x" * k)
    return k

def reader(n: int) -> str:
    try:
        return open("missing.txt").read()
    except FileNotFoundError:
        return "none"

def envy(n: int) -> str:
    return os.environ.get("BISIM_DEMO_VAR", "unset") + str(os.getenv("HOME") is not None)

def netty(n: int) -> int:
    socket.create_connection(("example.com", 80), timeout=1)
    return 1

def procy(n: int) -> int:
    subprocess.run(["echo", "hi"])
    return 1

def pure(n: int) -> int:
    return n + 1

def pathy(n: int) -> int:
    p = pathlib.Path("d") / "f.bin"
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(bytes([n % 256]))
    return len(p.read_bytes())
'''


def w(tmp_path, src, name="m.py"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return str(p)


def test_pure_functions_have_no_effects_key(tmp_path):
    m = w(tmp_path, SRC)
    assert run_probes(m, "pure", [Probe((1,))]) == [{"ok": True, "value": ["i", "2"]}]


def test_file_write_is_observed(tmp_path):
    m = w(tmp_path, SRC)
    (o,) = run_probes(m, "writer", [Probe((3,))])
    assert o["ok"] and o["value"] == ["i", "3"]
    assert ["open", "out.txt", "w"] in o["effects"]
    fs = [e for e in o["effects"] if e[0] == "fs"]
    assert fs and fs[0][1] == "out.txt" and len(fs[0][2]) == 64
    # different content → different effect
    (o2,) = run_probes(m, "writer", [Probe((4,))])
    assert [e for e in o2["effects"] if e[0] == "fs"][0][2] != fs[0][2]


def test_read_of_missing_file_and_pathlib(tmp_path):
    m = w(tmp_path, SRC)
    (o,) = run_probes(m, "reader", [Probe((1,))])
    assert o["value"] == ["s", "none"] and ["open", "missing.txt", "r"] in o["effects"]
    (o,) = run_probes(m, "pathy", [Probe((7,))])
    assert o["value"] == ["i", "1"] and any(e[0] == "fs" and e[1] == "d/f.bin" for e in o["effects"])


def test_env_reads_recorded_and_stable(tmp_path, monkeypatch):
    m = w(tmp_path, SRC)
    monkeypatch.setenv("BISIM_DEMO_VAR", "hello")
    (o,) = run_probes(m, "envy", [Probe((1,))])
    assert o["value"] == ["s", "helloTrue"]
    assert ["env", "BISIM_DEMO_VAR"] in o["effects"] and ["env", "HOME"] in o["effects"]


def test_network_and_subprocess_attempts_recorded_and_blocked(tmp_path):
    m = w(tmp_path, SRC)
    (o,) = run_probes(m, "netty", [Probe((1,))])
    assert o["ok"] is False and o["exc"] == "PermissionError" and ["net", "example.com", "80"] in o["effects"]
    (o,) = run_probes(m, "procy", [Probe((1,))])
    assert o["ok"] is False and o["exc"] == "PermissionError" and ["proc", "echo"] in o["effects"]


def test_effects_are_deterministic_and_in_the_address(tmp_path):
    m = w(tmp_path, SRC)
    observe(m, "writer", [Probe((3,)), Probe((0,))])  # 2× agreement, fresh cwd per probe
    a = hash_target(m, "writer", root=tmp_path).manifest.address
    # a refactor that writes the same content keeps the address
    m2 = w(tmp_path, SRC.replace('fh.write("x" * k)', 'fh.write("".join("x" for _ in range(k)))'), "m2.py")
    assert hash_target(m2, "writer", root=tmp_path).manifest.address == a
    # a mutant that writes different content (same return value) changes it
    m3 = w(tmp_path, SRC.replace('fh.write("x" * k)', 'fh.write("y" * k)'), "m3.py")
    d = diff_targets(m, "writer", m3, "writer", root=tmp_path)
    assert not d.same and "effects" in describe_obs(d.changes[0].old)


def test_probes_do_not_leak_files_between_each_other(tmp_path):
    m = w(tmp_path, SRC)
    obs = run_probes(m, "writer", [Probe((1,)), Probe((2,))])
    assert [e for e in obs[1]["effects"] if e[0] == "fs"] == [["fs", "out.txt", obs[1]["effects"][-1][2]]]
