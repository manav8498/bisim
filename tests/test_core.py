import pathlib
import textwrap

import pytest

from bisim.core import diff_functions, find_root, hash_function
from bisim.extract import Unsupported
from bisim.store import lookup, push
from bisim.witness import add_witness, load_ledger, save_ledger

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_hash_and_store(tmp_path):
    r = hash_function(str(FIX / "mutant_pairs" / "lt_to_le" / "a.py"), "f", root=tmp_path)
    assert r.manifest.address.startswith("bsm1:") and r.manifest.counts()["type"] >= 40
    push(tmp_path, r.manifest, r.spec.source, {"name": "f"})
    got = lookup(tmp_path, r.manifest.address)
    assert got["manifest"].address == r.manifest.address and "def f" in got["source"] and got["meta"]["name"] == "f"
    assert lookup(tmp_path, "bsm1:" + "0" * 64) is None


def test_diff_generated_vs_witnessed(tmp_path):
    a = FIX / "mutant_pairs" / "lt_to_le" / "a.py"
    b = FIX / "mutant_pairs" / "lt_to_le" / "b.py"
    d = diff_functions(str(a), "f", str(b), "f", root=tmp_path)
    assert not d.same and d.exit_code == 1
    zero = [c for c in d.changes if c.args == ["l", [["i", "0"]]]]
    assert zero and zero[0].kind == "type" and zero[0].old == {"ok": True, "value": ["B", 0]}
    L = load_ledger(tmp_path, str(a), "f")
    add_witness(L, [0], {"ok": True, "value": ["B", 0]}, "zero is not positive")
    save_ledger(tmp_path, str(a), "f", L)
    d2 = diff_functions(str(a), "f", str(b), "f", root=tmp_path)
    assert d2.exit_code == 2 and any(c.kind == "witness" and c.args == ["l", [["i", "0"]]] for c in d2.changes)


def test_diff_same_and_signature_change(tmp_path):
    a = FIX / "refactor_pairs" / "rename_locals" / "a.py"
    b = FIX / "refactor_pairs" / "rename_locals" / "b.py"
    d = diff_functions(str(a), "f", str(b), "f", root=tmp_path)
    assert d.same and d.exit_code == 0 and d.old_address == d.new_address
    m = tmp_path / "m.py"
    m.write_text("def f(a: str, b: int) -> int:\n    return b\n")
    d = diff_functions(str(a), "f", str(m), "f", root=tmp_path)
    assert d.signature_changed and d.exit_code == 2 and "signature changed" in d.warnings[0]


def test_hash_uses_ledger_probes(tmp_path):
    m = tmp_path / "m.py"
    m.write_text("def f(x: int) -> int:\n    return x + 1\n")
    L = load_ledger(tmp_path, str(m), "f")
    add_witness(L, [123456789], {"ok": True, "value": ["i", "123456790"]}, "big")
    save_ledger(tmp_path, str(m), "f", L)
    r = hash_function(str(m), "f", root=tmp_path)
    assert r.manifest.counts()["witness"] == 1


def test_witness_only_mode_for_unsupported_types(tmp_path):
    m = tmp_path / "m.py"
    m.write_text(
        textwrap.dedent(
            """
            class Thing:
                def __init__(self, v: int):
                    self.v = v
            def f(t: Thing) -> int:
                return t.v
            """
        )
    )
    with pytest.raises(Unsupported):
        hash_function(str(m), "f", root=tmp_path)


def test_dataclass_param_end_to_end(tmp_path):
    m = tmp_path / "m.py"
    m.write_text(
        textwrap.dedent(
            """
            import dataclasses
            @dataclasses.dataclass
            class Pt:
                x: int
                y: int
            def f(p: Pt) -> int:
                return p.x * p.y
            """
        )
    )
    r = hash_function(str(m), "f", root=tmp_path)
    assert r.manifest.counts()["type"] >= 20 and not r.warnings


def test_find_root(tmp_path):
    (tmp_path / ".bisim").mkdir()
    sub = tmp_path / "x" / "y"
    sub.mkdir(parents=True)
    assert find_root(str(sub)) == tmp_path.resolve()
