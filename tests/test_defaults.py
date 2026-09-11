from bisim.core import diff_targets, hash_target
from bisim.extract import extract_function, parse_signature
from bisim.probes import generate_type_probes


def test_defaults_are_extracted(tmp_path):
    m = tmp_path / "m.py"
    m.write_text("def f(a: int, b: int = 2, c: str = 'x') -> int:\n    return a + b + len(c)\n")
    s = extract_function(str(m), "f")
    assert s.params == [("a", "int"), ("b", "int"), ("c", "str")]
    assert s.defaults == {"b": "2", "c": "'x'"}
    assert parse_signature("def g(x: int = 1) -> int").defaults == {"x": "1"}


def test_probes_omit_trailing_defaults(tmp_path):
    s = parse_signature("def f(a: int, b: int = 2, c: str = 'x') -> int")
    ps = generate_type_probes(s)
    lengths = {len(p.args) for p in ps}
    assert lengths == {1, 2, 3}  # some probes leave out c, some leave out b and c
    # never omits a non-default parameter, never skips a middle parameter
    assert all(len(p.args) >= 1 for p in ps)
    s2 = parse_signature("def g(a: int) -> int")
    assert {len(p.args) for p in generate_type_probes(s2)} == {1}


def test_default_values_change_behavior_not_signature(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("def f(x: int = 1) -> int:\n    return x\n")
    b.write_text("def f(x: int = 2) -> int:\n    return x\n")
    d = diff_targets(str(a), "f", str(b), "f", root=tmp_path, grow=False)
    assert not d.signature_changed and not d.same
    assert any(c.args == ["l", []] for c in d.changes)  # the call with no arguments is the evidence
    assert hash_target(str(a), "f", root=tmp_path).manifest.address != hash_target(str(b), "f", root=tmp_path).manifest.address


def test_defaults_on_methods(tmp_path):
    m = tmp_path / "c.py"
    m.write_text("class C:\n    def __init__(self, n: int = 3):\n        self.n = n\n\n    def add(self, k: int = 1) -> int:\n        self.n += k\n        return self.n\n")
    r = hash_target(str(m), "C.add", root=tmp_path)
    inits = {len(p.args[0]) for p in r.probes}
    calls = {len(p.args[1][0][1]) for p in r.probes}
    assert inits == {0, 1} and calls == {0, 1}
