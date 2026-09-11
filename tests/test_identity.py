import json

from bisim.cli import main
from bisim.core import diff_targets, hash_target
from bisim.witness import add_witness, load_ledger, save_ledger

SRC = "def f(x: int) -> int:\n    if x == 424242:\n        return -1\n    return x + 1\n"
MUT = "def f(x: int) -> int:\n    if x == 424242:\n        return -2\n    return x + 1\n"


def test_witness_does_not_change_identity_but_sets_evidence(tmp_path):
    m = tmp_path / "m.py"
    m.write_text(SRC)
    before = hash_target(str(m), "f", root=tmp_path).manifest
    assert before.evidence == "" and before.standard_count == 48
    L = load_ledger(tmp_path, str(m), "f")
    add_witness(L, [424242], {"ok": True, "value": ["i", "-1"]}, "magic")
    save_ledger(tmp_path, str(m), "f", L)
    after = hash_target(str(m), "f", root=tmp_path).manifest
    assert after.address == before.address
    assert after.evidence.startswith("bse1:") and after.counts()["witness"] == 1


def test_growth_does_not_change_identity(tmp_path):
    m = tmp_path / "m.py"
    m.write_text(SRC)
    a = hash_target(str(m), "f", root=tmp_path, count=48).manifest
    b = hash_target(str(m), "f", root=tmp_path, count=192).manifest
    assert a.address == b.address and b.counts()["type"] == 192


def test_evidence_can_catch_what_identity_misses(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text(SRC)
    b.write_text(MUT)
    d = diff_targets(str(a), "f", str(b), "f", root=tmp_path, grow=False)
    assert d.same and d.old_address == d.new_address  # 48 standard probes never hit 424242
    L = load_ledger(tmp_path, str(a), "f")
    add_witness(L, [424242], {"ok": True, "value": ["i", "-1"]}, "magic")
    save_ledger(tmp_path, str(a), "f", L)
    d2 = diff_targets(str(a), "f", str(b), "f", root=tmp_path, grow=False)
    assert not d2.same and d2.exit_code == 2 and d2.old_address == d2.new_address
    assert d2.old_evidence != d2.new_evidence


def test_cli_shows_evidence(tmp_path, capsys):
    m = tmp_path / "m.py"
    m.write_text(SRC)
    main(["witness", "add", f"{m}:f", "--input", "[7]", "--run", "--root", str(tmp_path)])
    capsys.readouterr()
    code = main(["hash", f"{m}:f", "--json", "--root", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    assert code == 0 and d["address"].startswith("bsm1:") and d["evidence"].startswith("bse1:")
    code = main(["hash", f"{m}:f", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "evidence bse1:" in out
