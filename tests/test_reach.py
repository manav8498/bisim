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
