import json
import subprocess

from bisim.cli import main


def sh(*a, cwd):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path):
    sh("git", "init", "-q", cwd=tmp_path)
    (tmp_path / "m.py").write_text("def f(x: int) -> bool:\n    return x > 0\n\n\ndef g(x: int) -> int:\n    return x\n\n\ndef gone(x: int) -> int:\n    return x\n")
    (tmp_path / "other.py").write_text("def k(x: int) -> int:\n    return x\n")
    sh("git", "add", ".", cwd=tmp_path)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=tmp_path)


def test_check_reports_changed_added_removed_same(tmp_path, capsys):
    _repo(tmp_path)
    (tmp_path / "m.py").write_text(
        "def f(x: int) -> bool:\n    return x >= 0\n\n\ndef g(x: int) -> int:\n    y = x\n    return y\n\n\ndef h(x: int) -> int:\n    return 1\n"
    )
    (tmp_path / "new.py").write_text("def n(x: int) -> int:\n    return x * 2\n")
    code = main(["check", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "m.py:f" in out and "changed" in out
    assert "m.py:g" in out and "same" in out
    assert "m.py:h" in out and "added" in out
    assert "m.py:gone" in out and "removed" in out
    assert "new.py:n" in out and "added" in out
    assert "other.py" not in out


def test_check_json_and_witness_violation(tmp_path, capsys):
    _repo(tmp_path)
    main(["witness", "add", f"{tmp_path/'m.py'}:f", "--input", "[0]", "--run", "--note", "zero", "--root", str(tmp_path)])
    capsys.readouterr()
    (tmp_path / "m.py").write_text("def f(x: int) -> bool:\n    return x >= 0\n\n\ndef g(x: int) -> int:\n    return x\n\n\ndef gone(x: int) -> int:\n    return x\n")
    code = main(["check", "--json", "--root", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    assert code == 2
    f = next(r for r in d["results"] if r["name"] == "f")
    assert f["status"] == "witness-violation" and f["witnessed_changes"] == 1


def test_check_clean_tree(tmp_path, capsys):
    _repo(tmp_path)
    assert main(["check", "--root", str(tmp_path)]) == 0
    assert "nothing to check" in capsys.readouterr().out


def test_check_refused_function_is_reported_not_fatal(tmp_path, capsys):
    _repo(tmp_path)
    (tmp_path / "m.py").write_text("import random\n\n\ndef f(x: int) -> bool:\n    return random.random() > 0.5\n\n\ndef g(x: int) -> int:\n    return x\n\n\ndef gone(x: int) -> int:\n    return x\n")
    code = main(["check", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 3 and "refused" in out


def test_check_outside_git(tmp_path, capsys):
    assert main(["check", "--root", str(tmp_path)]) == 4
    assert "git" in capsys.readouterr().err
