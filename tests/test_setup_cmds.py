import json
import os
import stat
import subprocess

from bisim.cli import main


def test_init_creates_layout_and_config(tmp_path, capsys):
    code = main(["init", "--root", str(tmp_path), "--registry", "http://reg.example:8765"])
    out = capsys.readouterr().out
    assert code == 0 and (tmp_path / ".bisim" / "witness").is_dir() and (tmp_path / ".bisim" / "store").is_dir()
    cfg = json.loads((tmp_path / ".bisim" / "config.json").read_text())
    assert cfg["registry"] == "http://reg.example:8765" and cfg["count"] == 48
    assert ".bisim/store/" in (tmp_path / ".gitignore").read_text()
    # idempotent, keeps existing config
    code = main(["init", "--root", str(tmp_path)])
    assert code == 0 and json.loads((tmp_path / ".bisim" / "config.json").read_text())["registry"] == "http://reg.example:8765"


def test_install_hook(tmp_path, capsys):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    code = main(["install-hook", "--root", str(tmp_path)])
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert code == 0 and hook.exists() and os.access(hook, os.X_OK) and "bisim check" in hook.read_text()
    assert main(["install-hook", "--root", str(tmp_path)]) == 0  # idempotent


def test_install_hook_outside_git(tmp_path, capsys):
    assert main(["install-hook", "--root", str(tmp_path)]) == 4


def test_config_count_is_used(tmp_path, capsys):
    main(["init", "--root", str(tmp_path)])
    cfg = tmp_path / ".bisim" / "config.json"
    cfg.write_text(json.dumps({"count": 24}))
    m = tmp_path / "m.py"
    m.write_text("def f(x: int) -> int:\n    return x\n")
    capsys.readouterr()
    code = main(["hash", f"{m}:f", "--json", "--root", str(tmp_path)])
    assert code == 0 and json.loads(capsys.readouterr().out)["counts"]["type"] == 24
