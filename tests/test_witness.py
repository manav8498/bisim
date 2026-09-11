import json

from bisim.canon import canon
from bisim.extract import parse_signature
from bisim.witness import add_witness, ledger_path, ledger_probes, load_ledger, save_ledger


def test_path_layout(tmp_path):
    p = ledger_path(tmp_path, str(tmp_path / "pkg" / "m.py"), "f")
    assert p == tmp_path / ".bisim" / "witness" / "pkg" / "m" / "f.json"
    ext = ledger_path(tmp_path, "/somewhere/else/x.py", "f")
    assert ext == tmp_path / ".bisim" / "witness" / "_external" / "x" / "f.json"


def test_roundtrip_and_probes(tmp_path):
    mp = str(tmp_path / "m.py")
    L = load_ledger(tmp_path, mp, "f", parse_signature("def f(xs: list[float]) -> float"))
    assert L.witnesses == [] and L.function == "f" and L.signature == "(xs: list[float]) -> float"
    add_witness(L, [[1, 2, 3, 4]], {"ok": True, "value": ["f", "2.5"]}, "even", "mint")
    L.suggested.append(canon([[]]))
    L.suggested.append(canon([[1, 2, 3, 4]]))  # duplicate of the witness → collapses
    L.excluded.append({"args": canon([[7]]), "reason": "x"})
    L.suggested.append(canon([[7]]))  # excluded → dropped
    save_ledger(tmp_path, mp, "f", L)
    L2 = load_ledger(tmp_path, mp, "f")
    assert L2.witnesses[0].args == canon([[1, 2, 3, 4]]) and L2.witnesses[0].note == "even"
    assert L2.witnesses[0].source == "mint" and L2.witnesses[0].at
    ps = ledger_probes(L2)
    assert [(p.kind, p.args) for p in ps] == [("witness", ([1, 2, 3, 4],)), ("suggested", ([],))]


def test_add_witness_replaces_same_args(tmp_path):
    L = load_ledger(tmp_path, str(tmp_path / "m.py"), "f")
    add_witness(L, [0], {"ok": True, "value": ["B", 0]}, "first")
    add_witness(L, [0], {"ok": True, "value": ["B", 1]}, "second")
    assert len(L.witnesses) == 1 and L.witnesses[0].note == "second"


def test_json_format(tmp_path):
    mp = str(tmp_path / "m.py")
    save_ledger(tmp_path, mp, "f", load_ledger(tmp_path, mp, "f"))
    d = json.loads(ledger_path(tmp_path, mp, "f").read_text())
    assert d["format"] == "bisim-witness/1" and d["witnesses"] == []
