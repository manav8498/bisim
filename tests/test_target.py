import textwrap

import pytest

from bisim.core import diff_targets, hash_target
from bisim.extract import Unsupported
from bisim.fmt import describe_obs, fmt_args
from bisim.target import Target

STACK = '''
class Stack:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.items: list[int] = []

    def push(self, x: int) -> None:
        if len(self.items) >= self.capacity:
            raise OverflowError("full")
        self.items.append(x)

    def pop(self) -> int:
        if not self.items:
            raise IndexError("empty")
        return self.items.pop()

    def size(self) -> int:
        return len(self.items)
'''
# behavior-preserving rewrite
STACK2 = STACK.replace("        if not self.items:\n            raise IndexError(\"empty\")\n        return self.items.pop()",
                       "        if len(self.items) == 0:\n            raise IndexError(\"nothing to pop\")\n        last = self.items[-1]\n        del self.items[-1]\n        return last")
# mutant: pop returns the *first* element (FIFO)
STACK3 = STACK.replace("        return self.items.pop()", "        return self.items.pop(0)")


def w(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return str(p)


def test_target_load_kinds(tmp_path):
    p = w(tmp_path, "m.py", STACK)
    assert Target.load(p, "Stack").kind == "class"
    t = Target.load(p, "Stack.push")
    assert t.kind == "method" and t.name == "Stack.push" and t.signature_str() == "Stack(capacity: int).push(x: int) -> None"
    with pytest.raises(Unsupported):
        Target.load(p, "Stack.nope")


def test_method_observation_carries_state(tmp_path):
    p = w(tmp_path, "m.py", STACK)
    r = hash_target(p, "Stack.push", root=tmp_path)
    m = r.manifest
    assert m.address.startswith("bsm1:") and m.function["kind"] == "method" and m.counts()["type"] >= 40
    rec = next(pr for pr in m.probes if pr.obs.get("ok") and pr.obs["steps"][0].get("ok"))
    assert rec.obs["steps"][0]["value"] == ["n"] and rec.obs["state"][0] == "d"  # None returned; dict state
    assert any(pr.obs.get("ok") and not pr.obs["steps"][0].get("ok") for pr in m.probes)  # OverflowError on capacity 0
    assert m.coverage and m.coverage["pct"] == 100.0


def test_class_sequences_and_refactor_vs_mutant(tmp_path):
    a, b, c = w(tmp_path, "a.py", STACK), w(tmp_path, "b.py", STACK2), w(tmp_path, "c.py", STACK3)
    ha = hash_target(a, "Stack", root=tmp_path).manifest
    assert ha.function["kind"] == "class" and ha.function["methods"] == ["pop", "push", "size"]
    assert any(len(pr.obs.get("steps", [])) >= 2 for pr in ha.probes)  # real sequences ran
    d = diff_targets(a, "Stack", b, "Stack", root=tmp_path)
    assert d.same and d.exit_code == 0, [describe_obs(c.old) + " vs " + describe_obs(c.new) for c in d.changes[:3]]
    d = diff_targets(a, "Stack", c, "Stack", root=tmp_path)
    assert not d.same and d.exit_code == 1
    ev = d.changes[0]
    assert "push" in fmt_args(ev.args) and "pop" in fmt_args(ev.args)
    # the method-level view agrees: pop on a fresh instance is identical (empty → IndexError); sequences differ
    assert diff_targets(a, "Stack.pop", c, "Stack.pop", root=tmp_path).same


def test_dataclass_method_target(tmp_path):
    p = w(tmp_path, "d.py", "from dataclasses import dataclass\n\n@dataclass\nclass Pt:\n    x: int\n    y: int = 0\n\n    def norm1(self) -> int:\n        return abs(self.x) + abs(self.y)\n\n    def shift(self, d: int) -> None:\n        self.x += d\n")
    r = hash_target(p, "Pt.shift", root=tmp_path)
    rec = r.manifest.probes[0].obs
    assert rec["ok"] and rec["state"][0] == "D" and rec["state"][1] == "Pt"
    assert hash_target(p, "Pt", root=tmp_path).manifest.function["methods"] == ["norm1", "shift"]


def test_fmt_for_class_probes():
    from bisim.canon import canon

    assert fmt_args(canon([(3,), (("push", (7,)), ("pop", ()))])) == "new(3); push(7); pop()"
    assert describe_obs({"ok": False, "exc": "ValueError", "at": "init"}) == "init raises ValueError"
    assert describe_obs({"ok": True, "steps": [{"ok": True, "value": ["n"]}, {"ok": False, "exc": "IndexError"}], "state": ["d", []]}) == "None; raises IndexError"
    assert describe_obs({"ok": True, "steps": [{"ok": True, "value": ["n"]}], "state": ["d", [[["s", "items"], ["l", []]]]]}) == "None  → state {'items': []}"


def test_cli_class_targets_end_to_end(tmp_path, capsys):
    import json
    import subprocess

    from bisim.cli import main

    a = w(tmp_path, "a.py", STACK)
    c = w(tmp_path, "c.py", STACK3)
    code = main(["hash", f"{a}:Stack.push", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0 and "Stack(capacity: int).push(x: int) -> None" in out and "lines 100.0%" in out
    code = main(["diff", f"{a}:Stack", f"{c}:Stack", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1 and "CHANGED" in out and "push(" in out and "→ state" in out
    # witness a sequence, then the mutant violates it
    code = main(["witness", "add", f"{a}:Stack", "--init", "[2]", "--calls", '[["push", [1]], ["push", [2]], ["pop", []]]', "--run",
                 "--note", "LIFO", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0 and "new(2); push(1); push(2); pop()" in out and "2" in out
    code = main(["diff", f"{a}:Stack", f"{c}:Stack", "--json", "--root", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    assert code == 2 and any(ch["kind"] == "witness" for ch in d["changes"])
    # method witness
    code = main(["witness", "add", f"{a}:Stack.push", "--init", "[0]", "--input", "[5]", "--raises", "OverflowError", "--root", str(tmp_path)])
    assert code == 0
    capsys.readouterr()
    code = main(["hash", f"{a}:Stack.push", "--json", "--root", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["counts"]["witness"] == 1
    # check in a git repo pairs Class and Class.method targets
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text(textwrap.dedent(STACK3))
    code = main(["check", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 2 and "a.py:Stack " in out and "WITNESS-VIOLATION" in out and "a.py:Stack.pop" in out and "a.py:Stack.push" in out
