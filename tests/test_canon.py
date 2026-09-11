import dataclasses
import math

from bisim.canon import canon, canon_json, is_opaque, parse_literal, sha256_hex, uncanon


def test_ints_are_decimal_strings():
    assert canon(5) == ["i", "5"]
    assert canon(2**70) == ["i", str(2**70)]


def test_bool_is_not_int():
    assert canon(True) == ["B", 1] and canon(1) == ["i", "1"]


def test_float_special_cases():
    assert canon(float("nan")) == ["f", "nan"]
    assert canon(-0.0) == ["f", "-0.0"]
    assert canon(float("inf")) == ["f", "inf"]
    assert canon(float("-inf")) == ["f", "-inf"]
    assert canon(0.1) == ["f", "0.1"]


def test_containers_and_sets_sorted():
    assert canon((1, "a")) == ["t", [["i", "1"], ["s", "a"]]]
    assert canon({3, 1, 2}) == ["S", [["i", "1"], ["i", "2"], ["i", "3"]]]
    assert canon({"b": 1, "a": 2}) == ["d", [[["s", "a"], ["i", "2"]], [["s", "b"], ["i", "1"]]]]


def test_bytes_and_none():
    assert canon(b"\x00ab") == ["b", "006162"] and canon(None) == ["n"]


def test_dataclass():
    @dataclasses.dataclass
    class P:
        x: int
        y: str

    c = canon(P(1, "z"))
    assert c[0] == "D" and c[1].endswith("P") and c[2] == [["x", ["i", "1"]], ["y", ["s", "z"]]]


def test_objects_are_structural_not_opaque():
    class Q:
        def __init__(self):
            self.a = 1
            self._hidden = 2

    assert canon(Q()) == ["o", "test_objects_are_structural_not_opaque.<locals>.Q", [["a", ["i", "1"]]]]
    assert not is_opaque(canon(Q()))
    assert is_opaque(canon(object()))


def test_opaque_flag():
    class Q:
        __slots__ = ()

    assert is_opaque(canon([Q()])) is False and canon(Q()) == ["o", "test_opaque_flag.<locals>.Q", []]
    assert is_opaque(canon([object()]))
    assert is_opaque(canon({"k": (object(),)}))
    assert not is_opaque(canon([1, 2]))
    assert not is_opaque(canon({"k": [1, {2}]}))


def test_roundtrip():
    v = [1, -0.0, "é", b"\x01", None, (1, 2), {1, 2}, {"k": [True, 2**64]}]
    assert canon(uncanon(canon(v))) == canon(v)
    assert math.isnan(uncanon(canon(float("nan"))))


def test_canon_json_stable():
    assert canon_json({"b": 1, "a": 2}) == canon_json({"a": 2, "b": 1})
    assert sha256_hex("x") == sha256_hex(b"x")


def test_parse_literal():
    assert parse_literal("[1, 2]") == [1, 2]
    assert parse_literal("(1, 'a')") == (1, "a")
    assert parse_literal('{"a": 1}') == {"a": 1}
