import textwrap

import pytest

from bisim.extract import NotFound, Unsupported, extract_function, extract_functions, parse_signature


def w(tmp_path, src):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(src))
    return str(p)


def test_extract_basic(tmp_path):
    p = w(
        tmp_path,
        '''
        def add(a: int, b: int) -> int:
            """sum"""
            return a + b
        ''',
    )
    s = extract_function(p, "add")
    assert s.params == [("a", "int"), ("b", "int")] and s.returns == "int"
    assert s.signature_str() == "(a: int, b: int) -> int"
    assert "return a + b" in s.source and s.source.startswith("def add")
    assert s.module_path == p and s.lineno == 2


def test_missing_hint_unsupported(tmp_path):
    p = w(tmp_path, "def f(a, b: int) -> int:\n    return b\n")
    with pytest.raises(Unsupported, match="type hint"):
        extract_function(p, "f")
    p2 = w(tmp_path, "def f(a: int):\n    return a\n")
    with pytest.raises(Unsupported, match="return type"):
        extract_function(p2, "f")


def test_async_generator_varargs_method_unsupported(tmp_path):
    p = w(
        tmp_path,
        """
        async def g(x: int) -> int: return x
        def h(x: int) -> int:
            yield x
        def v(*a: int) -> int: return 0
        def k(x: int, *, y: int) -> int: return 0
        class C:
            def m(self, x: int) -> int: return x
        """,
    )
    for n in ("g", "h", "v", "k"):
        with pytest.raises(Unsupported):
            extract_function(p, n)
    with pytest.raises(NotFound):
        extract_function(p, "m")
    assert extract_functions(p) == []


def test_extract_functions_skips_unsupported(tmp_path):
    p = w(tmp_path, "def ok(x: int) -> int:\n    return x\n\ndef bad(x):\n    return x\n")
    assert [s.name for s in extract_functions(p)] == ["ok"]


def test_parse_signature():
    s = parse_signature("def median(xs: list[float]) -> float")
    assert s.name == "median" and s.params == [("xs", "list[float]")] and s.returns == "float"
    assert s.source == "" and s.module_path == ""
    s2 = parse_signature("def f(a: int, b: str) -> bool:")
    assert s2.signature_str() == "(a: int, b: str) -> bool"
