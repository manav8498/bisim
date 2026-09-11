import textwrap

import pytest

from bisim.extract import Unsupported, extract_class, parse_target
from bisim.probes import generate_method_probes, generate_sequence_probes

STACK = '''
class Stack:
    """LIFO with a capacity."""

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

    def _secret(self, x: int) -> int:
        return x

    @staticmethod
    def make() -> "Stack":
        return Stack(1)

    def untyped(self, x) -> int:
        return 1
'''


@pytest.fixture
def mod(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(STACK))
    return str(p)


def test_parse_target():
    assert parse_target("f") == (None, "f")
    assert parse_target("Stack.pop") == ("Stack", "pop")
    assert parse_target("Stack") == ("Stack", None)
    with pytest.raises(Unsupported):
        parse_target("a.b.c")


def test_extract_class(mod):
    c = extract_class(mod, "Stack")
    assert c.name == "Stack" and c.init_params == [("capacity", "int")] and not c.is_dataclass
    assert sorted(c.methods) == ["_secret", "make", "pop", "push", "size"]  # untyped skipped; static kept
    assert c.public_methods() == ["make", "pop", "push", "size"] and c.kinds["make"] == "static"
    assert c.methods["push"].params == [("x", "int")] and c.methods["push"].returns == "None"
    assert c.methods["pop"].params == [] and c.lines  # executable lines of the whole class body


def test_extract_dataclass(tmp_path):
    p = tmp_path / "d.py"
    p.write_text("from dataclasses import dataclass\n\n@dataclass\nclass Pt:\n    x: int\n    y: int = 0\n\n    def norm1(self) -> int:\n        return abs(self.x) + abs(self.y)\n")
    c = extract_class(str(p), "Pt")
    assert c.is_dataclass and c.init_params == [("x", "int"), ("y", "int")] and list(c.methods) == ["norm1"]


def test_extract_class_no_init(tmp_path):
    p = tmp_path / "n.py"
    p.write_text("class Counter:\n    n = 0\n\n    def inc(self) -> int:\n        self.n += 1\n        return self.n\n")
    c = extract_class(str(p), "Counter")
    assert c.init_params == [] and list(c.methods) == ["inc"]


def test_method_and_sequence_probes(mod):
    c = extract_class(mod, "Stack")
    mp = generate_method_probes(c, "push")
    assert len(mp) >= 40 and all(len(p.args) == 2 for p in mp)
    init, calls = mp[0].args
    assert isinstance(init, tuple) and len(init) == 1 and calls == (("push", (calls[0][1][0],)),)
    assert len({p.id for p in mp}) == len(mp)
    sp = generate_sequence_probes(c)
    assert len(sp) >= 40
    lens = {len(p.args[1]) for p in sp}
    assert min(lens) == 1 and max(lens) <= 4
    names = {m for p in sp for m, _ in p.args[1]}
    assert names == {"make", "pop", "push", "size"}  # private methods never appear in sequences
    # determinism
    assert [p.id for p in generate_sequence_probes(c)] == [p.id for p in sp]
