import json
import textwrap

from bisim.cli import main
from bisim.core import hash_function
from bisim.reach import parse_inputs, reach

NARROW = """def f(x: int) -> int:
    if 7000 < x < 7100:
        return 1
    if x == -123456:
        return 2
    return 0
"""


class Stub:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_parse_inputs_tolerates_fences():
    assert parse_inputs('```json\n{"inputs": [[7050], [5]]}\n```') == [[7050], [5]]
    assert parse_inputs("garbage") == []


def test_reach_keeps_only_inputs_that_hit_missed_lines(tmp_path):
    m = tmp_path / "m.py"
    m.write_text(NARROW)
    before = hash_function(str(m), "f", root=tmp_path).manifest.coverage
    assert before["missed"] == [3, 5]
    stub = Stub(['{"inputs": [[7050], [5], [-123456], "junk", [1, 2]]}'])
    r = reach(str(m), "f", stub, root=tmp_path)
    assert r.added == 2 and sorted(r.reached) == [3, 5] and r.rejected == 2  # [5] hit nothing; [1,2] wrong arity; 'junk' dropped at parse
    assert "line 3" in stub.prompts[0] and "7000 < x < 7100" in stub.prompts[0]
    after = hash_function(str(m), "f", root=tmp_path).manifest
    assert after.coverage["pct"] == 100.0 and after.counts()["suggested"] == 2
    assert after.address != r.address_after or after.address == r.address_after  # address is stable across re-hash
    assert after.address == r.address_after


def test_reach_stops_when_nothing_missed(tmp_path):
    m = tmp_path / "m.py"
    m.write_text("def f(x: int) -> int:\n    return x\n")
    stub = Stub([])
    r = reach(str(m), "f", stub, root=tmp_path)
    assert r.added == 0 and r.missed_before == [] and stub.prompts == []


def test_reach_rounds_until_covered_or_exhausted(tmp_path):
    m = tmp_path / "m.py"
    m.write_text(NARROW)
    stub = Stub(['{"inputs": [[7050]]}', '{"inputs": [[-123456]]}', '{"inputs": []}'])
    r = reach(str(m), "f", stub, root=tmp_path, rounds=3)
    assert r.added == 2 and r.missed_after == [] and len(stub.prompts) == 2


def test_reach_cli(tmp_path, capsys, monkeypatch):
    m = tmp_path / "m.py"
    m.write_text(NARROW)
    import bisim.reachcli as rc

    monkeypatch.setattr(rc, "default_client", lambda model=None, prefer="auto": Stub(['{"inputs": [[7050], [-123456]]}']))
    code = main(["reach", f"{m}:f", "--json", "--root", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    assert code == 0 and d["added"] == 2 and d["coverage_after"]["pct"] == 100.0


STACKY = """class Stack:
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
        if self.items[-1] == 424242:
            return -1
        return self.items.pop()
"""


def test_reach_class_and_method_targets(tmp_path):
    m = tmp_path / "s.py"
    m.write_text(STACKY)
    before = hash_function(str(m), "Stack.pop", root=tmp_path).manifest.coverage
    assert 14 in before["missed"] and 15 in before["missed"]  # a fresh stack cannot be popped at all
    stub = Stub(['{"inputs": [{"init": [3], "args": []}, {"init": [0], "args": []}]}'])
    r = reach(str(m), "Stack.pop", stub, root=tmp_path, rounds=1)
    assert r.added == 0 and r.rejected == 2  # neither reaches the missed lines: nothing to pop
    stub = Stub(['{"inputs": [{"init": [5], "calls": [["push", [424242]], ["pop", []]]}, {"init": [5], "calls": [["push", [1]], ["pop", []]]}, [1, 2]]}'])
    assert hash_function(str(m), "Stack", root=tmp_path).manifest.coverage["missed"] == [15]  # sequences already pop
    r = reach(str(m), "Stack", stub, root=tmp_path, rounds=1)
    assert r.added == 1 and r.rejected == 2 and r.reached == [15]  # push(1);pop() reaches nothing new
    assert "class Stack" in stub.prompts[0] and '"init"' in stub.prompts[0] and '"calls"' in stub.prompts[0]
    after = hash_function(str(m), "Stack", root=tmp_path).manifest
    assert after.counts()["suggested"] == 1 and after.coverage["missed"] == []
