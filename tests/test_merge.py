import json
import subprocess

from bisim.cli import main


def sh(*a, cwd):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


def commit(cwd, msg):
    sh("git", "add", ".", cwd=cwd)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg, cwd=cwd)


BASE = """def f(x: int) -> int:
    return x


def g(x: int) -> int:
    return x


def h(x: int) -> int:
    return x


def k(x: int) -> int:
    return x


def same(x: int) -> int:
    return x
"""
# ours: f changes on positives; g changes; h changes (identically to theirs); k changes on negatives
OURS = BASE.replace("def f(x: int) -> int:\n    return x", "def f(x: int) -> int:\n    return x + 1 if x > 0 else x") \
           .replace("def g(x: int) -> int:\n    return x", "def g(x: int) -> int:\n    return -x") \
           .replace("def h(x: int) -> int:\n    return x", "def h(x: int) -> int:\n    return x * 2") \
           .replace("def k(x: int) -> int:\n    return x", "def k(x: int) -> int:\n    return 0 if x < 0 else x")
# theirs: f changes on positives DIFFERENTLY; h identically; k changes only at zero (disjoint from ours)
THEIRS = BASE.replace("def f(x: int) -> int:\n    return x", "def f(x: int) -> int:\n    return x + 2 if x > 0 else x") \
             .replace("def h(x: int) -> int:\n    return x", "def h(x: int) -> int:\n    return 2 * x") \
             .replace("def k(x: int) -> int:\n    return x", "def k(x: int) -> int:\n    return 99 if x == 0 else x") \
             + "\n\ndef added(x: int) -> int:\n    return x\n"


def _repo(tmp_path):
    sh("git", "init", "-q", "-b", "main", cwd=tmp_path)
    (tmp_path / "m.py").write_text(BASE)
    commit(tmp_path, "base")
    sh("git", "checkout", "-q", "-b", "ours", cwd=tmp_path)
    (tmp_path / "m.py").write_text(OURS)
    commit(tmp_path, "ours")
    sh("git", "checkout", "-q", "main", cwd=tmp_path)
    sh("git", "checkout", "-q", "-b", "theirs", cwd=tmp_path)
    (tmp_path / "m.py").write_text(THEIRS)
    commit(tmp_path, "theirs")
    sh("git", "checkout", "-q", "ours", cwd=tmp_path)


def test_merge_check_classifies_every_case(tmp_path, capsys):
    _repo(tmp_path)
    code = main(["merge-check", "main", "--ours", "ours", "--theirs", "theirs", "--json", "--root", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    by = {r["name"]: r for r in d["results"]}
    assert code == 2
    assert by["f"]["status"] == "conflict" and by["f"]["conflicts"] >= 1
    ev = by["f"]["evidence"][0]
    assert "ours" in ev and "theirs" in ev and "base" in ev
    assert by["g"]["status"] == "ours-only"
    assert by["h"]["status"] == "convergent"
    assert by["k"]["status"] == "independent"
    assert by["added"]["status"] == "theirs-only"
    assert "same" not in by  # untouched functions are not reported


def test_merge_check_human_output_and_clean(tmp_path, capsys):
    _repo(tmp_path)
    code = main(["merge-check", "main", "--ours", "ours", "--theirs", "theirs", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 2 and "CONFLICT" in out and "m.py:f" in out and "(1,)" in out
    # ours vs ours: nothing to do
    code = main(["merge-check", "main", "--ours", "ours", "--theirs", "ours", "--root", str(tmp_path)])
    assert code == 0


def test_merge_check_delete_modify(tmp_path, capsys):
    _repo(tmp_path)
    sh("git", "checkout", "-q", "-b", "deleter", "main", cwd=tmp_path)
    (tmp_path / "m.py").write_text(BASE.replace("def f(x: int) -> int:\n    return x\n\n\n", ""))
    commit(tmp_path, "delete f")
    code = main(["merge-check", "main", "--ours", "ours", "--theirs", "deleter", "--json", "--root", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    assert next(r for r in d["results"] if r["name"] == "f")["status"] == "delete-modify" and code == 2
