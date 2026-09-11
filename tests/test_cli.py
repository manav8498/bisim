import json
import pathlib
import subprocess
import sys

from bisim.cli import main

FIX = pathlib.Path(__file__).parent / "fixtures"
LT = FIX / "mutant_pairs" / "lt_to_le"
RN = FIX / "refactor_pairs" / "rename_locals"


def run(args, capsys):
    code = main(args)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_hash_human_and_json(capsys, tmp_path):
    code, out, _ = run(["hash", f"{LT/'a.py'}:f", "--root", str(tmp_path)], capsys)
    assert code == 0 and "bsm1:" in out and "probes=" in out
    code, out, _ = run(["hash", f"{LT/'a.py'}:f", "--json", "--root", str(tmp_path)], capsys)
    d = json.loads(out)
    assert code == 0 and d["address"].startswith("bsm1:") and d["counts"]["type"] >= 40 and "manifest" in d


def test_diff_exit_codes_and_witness_flow(capsys, tmp_path):
    a, b = LT / "a.py", LT / "b.py"
    code, out, _ = run(["diff", f"{a}:f", f"{b}:f", "--root", str(tmp_path)], capsys)
    assert code == 1 and "CHANGED" in out and "(0,)" in out
    code, out, _ = run(["diff", f"{RN/'a.py'}:f", f"{RN/'b.py'}:f", "--root", str(tmp_path)], capsys)
    assert code == 0 and "SAME" in out
    code, out, _ = run(
        ["witness", "add", f"{a}:f", "--input", "[0]", "--run", "--note", "zero is not positive", "--root", str(tmp_path)],
        capsys,
    )
    assert code == 0 and "witness" in out.lower()
    code, out, _ = run(["diff", f"{a}:f", f"{b}:f", "--root", str(tmp_path)], capsys)
    assert code == 2 and "witness" in out.lower()
    code, out, _ = run(["diff", f"{a}:f", f"{b}:f", "--json", "--root", str(tmp_path)], capsys)
    d = json.loads(out)
    assert d["exit_code"] == 2 and any(c["kind"] == "witness" for c in d["changes"])
    code, out, _ = run(["witness", "list", f"{a}:f", "--root", str(tmp_path)], capsys)
    assert code == 0 and "zero is not positive" in out


def test_witness_add_with_expect(capsys, tmp_path):
    a = LT / "a.py"
    code, out, _ = run(["witness", "add", f"{a}:f", "--input", "[5]", "--expect", "true", "--root", str(tmp_path)], capsys)
    assert code == 0
    code, out, _ = run(["witness", "add", f"{a}:f", "--input", "[-5]", "--expect", "false", "--root", str(tmp_path)], capsys)
    assert code == 0
    code, out, _ = run(["hash", f"{a}:f", "--json", "--root", str(tmp_path)], capsys)
    assert json.loads(out)["counts"]["witness"] == 2


def test_refused_and_error_codes(capsys, tmp_path):
    m = tmp_path / "m.py"
    m.write_text("import random\ndef f(x: int) -> float:\n    return random.random()\ndef g(x) -> int:\n    return 1\n")
    code, out, err = run(["hash", f"{m}:f", "--root", str(tmp_path)], capsys)
    assert code == 3 and "nondeterministic" in err
    code, out, err = run(["hash", f"{m}:g", "--root", str(tmp_path)], capsys)
    assert code == 3 and "type hint" in err
    code, out, err = run(["hash", f"{m}:zzz", "--root", str(tmp_path)], capsys)
    assert code == 4 and "no top-level function" in err
    code, out, err = run(["hash", "nofile.py:f", "--root", str(tmp_path)], capsys)
    assert code == 4
    code, out, err = run(["hash", "bad-target", "--root", str(tmp_path)], capsys)
    assert code == 4 and "path.py:function" in err


def test_push_lookup(capsys, tmp_path):
    a = LT / "a.py"
    code, out, _ = run(["hash", f"{a}:f", "--push", "--json", "--root", str(tmp_path)], capsys)
    addr = json.loads(out)["address"]
    code, out, _ = run(["lookup", addr, "--root", str(tmp_path)], capsys)
    assert code == 0 and "def f" in out
    code, out, err = run(["lookup", "bsm1:" + "0" * 64, "--root", str(tmp_path)], capsys)
    assert code == 4 and "not found" in err


def test_console_script_and_module():
    assert subprocess.run([sys.executable, "-m", "bisim.cli", "--help"], capture_output=True).returncode == 0
    r = subprocess.run([sys.executable, "-m", "bisim.cli"], capture_output=True)
    assert r.returncode == 4
