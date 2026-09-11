import pathlib

import pytest

from bisim.address import PREFIX, Manifest, build_manifest, compute_address, merkle_root, sig_hash
from bisim.extract import extract_function, parse_signature
from bisim.probes import generate_type_probes
from bisim.sandbox import observe, run_probes

FIX = pathlib.Path(__file__).parent / "fixtures"


def addr(path, name=None):
    from bisim.core import hash_target

    name = name or ("C" if "class_" in str(path) else "f")
    r = hash_target(str(path), name, root=path.parent)
    return r.manifest, r.probes


def _rerun(path, probe):
    from bisim.target import Target

    t = Target.load(str(path), "C" if "class_" in str(path) else "f")
    return t.observe([probe])[0]


def test_sig_hash_ignores_names():
    assert sig_hash(parse_signature("def f(a: int) -> int")) == sig_hash(parse_signature("def g(b: int) -> int"))
    assert sig_hash(parse_signature("def f(a: int) -> int")) != sig_hash(parse_signature("def f(a: str) -> int"))


def test_merkle_root_properties():
    assert merkle_root(["b", "a"]) == merkle_root(["a", "b"])
    assert merkle_root([]) == merkle_root([])
    assert merkle_root(["a"]) != merkle_root(["a", "a"])
    assert merkle_root(["a", "b"]) != merkle_root(["a", "c"])


def test_compute_address_depends_on_everything():
    base = compute_address("s", "r", "v1")
    assert base.startswith(PREFIX) and len(base) == len(PREFIX) + 64
    assert base != compute_address("t", "r", "v1") != compute_address("s", "q", "v1") != compute_address("s", "r", "v2")


def test_manifest_roundtrip_and_format():
    m, _ = addr(FIX / "refactor_pairs" / "loop_to_comprehension" / "a.py")
    assert m.address.startswith(PREFIX) and len(m.probes) >= 40
    d = m.to_dict()
    assert d["format"] == "bisim-manifest/1" and d["probegen"] == "v1" and d["function"]["name"] == "f"
    assert Manifest.from_dict(d).address == m.address and m.short() == m.address[: len(PREFIX) + 12]


@pytest.mark.parametrize("pair", sorted(p.name for p in (FIX / "refactor_pairs").iterdir()))
def test_refactor_pairs_preserve_address(pair):
    a, _ = addr(FIX / "refactor_pairs" / pair / "a.py")
    b, _ = addr(FIX / "refactor_pairs" / pair / "b.py")
    assert a.address == b.address, pair


@pytest.mark.parametrize("pair", sorted(p.name for p in (FIX / "mutant_pairs").iterdir()))
def test_mutant_pairs_flip_with_evidence(pair):
    pa, pb = FIX / "mutant_pairs" / pair / "a.py", FIX / "mutant_pairs" / pair / "b.py"
    a, probes = addr(pa)
    b, _ = addr(pb)
    assert a.address != b.address, pair
    diffs = [(x, y) for x, y in zip(a.probes, b.probes) if x.obs_hash != y.obs_hash]
    assert diffs, pair
    # the evidence must reproduce: rerun exactly the first differing probe on both sides
    p = next(pr for pr in probes if pr.id == diffs[0][0].id)
    assert _rerun(pa, p) != _rerun(pb, p), pair
