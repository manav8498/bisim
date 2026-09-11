import textwrap

from bisim.core import diff_targets, hash_target
from bisim.extract import extract_class, extract_function

SRC = '''
def f(x: int, ys: list[int]) -> int:
    total = 0
    if x > 100:
        total += 1
    elif x < -100:
        total -= 1
    for y in ys:
        if y == 424242:
            total += 1000
    while total > 5:
        total -= 5
    return total
'''


def test_decisions_extracted(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(SRC))
    s = extract_function(str(p), "f")
    lines = {d["line"]: d for d in s.decisions}
    assert set(lines) == {4, 6, 8, 9, 11}
    assert lines[4]["body"] == [5] and lines[4]["kind"] == "if"
    assert lines[8]["kind"] == "for" and lines[8]["body"] == [9, 10]
    assert lines[11]["kind"] == "while" and lines[11]["body"] == [12]


def test_branch_coverage_reported(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(SRC))
    m = hash_target(str(p), "f", root=tmp_path).manifest
    b = m.coverage["branches"]
    assert b["total"] == 10  # 5 decisions × 2 outcomes
    assert "L9 true" in b["missed"]  # y == 424242 never true
    assert b["hit"] < b["total"] and 0 < b["pct"] < 100
    assert m.coverage["pct"] < 100  # line 10 also unreached


def test_branch_coverage_full_when_everything_runs(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("def g(x: int) -> int:\n    if x > 0:\n        return 1\n    return 0\n")
    m = hash_target(str(p), "g", root=tmp_path).manifest
    assert m.coverage["branches"] == {"total": 2, "hit": 2, "pct": 100.0, "missed": []}


def test_class_decisions_aggregate(tmp_path):
    p = tmp_path / "c.py"
    p.write_text("class C:\n    def __init__(self, n: int):\n        self.n = n\n\n    def m(self) -> int:\n        if self.n > 0:\n            return 1\n        return 0\n")
    c = extract_class(str(p), "C")
    assert [d["line"] for d in c.decisions] == [6]
    m = hash_target(str(p), "C", root=tmp_path).manifest
    assert m.coverage["branches"]["pct"] == 100.0


def test_diff_grows_on_missed_branches(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("def f(x: int) -> int:\n    if 7000 < x < 7100:\n        return 1\n    return 0\n")
    b.write_text("def f(x: int) -> int:\n    return 0\n")
    d = diff_targets(str(a), "f", str(b), "f", root=tmp_path)
    assert d.growth_rounds >= 1 and any("L2 true" in w for w in d.warnings)
