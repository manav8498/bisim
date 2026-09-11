import json
import math
import subprocess
import sys

import pytest

from bisim.canon import canon
from bisim.extract import Unsupported, parse_signature
from bisim.probes import PROBEGEN_VERSION, Probe, generate_type_probes, strategy_for


def ids(spec, n=48):
    return [p.id for p in generate_type_probes(spec, n)]


def test_deterministic_in_process():
    s = parse_signature("def f(xs: list[int], k: str) -> int")
    assert ids(s) == ids(s)


def test_deterministic_across_processes():
    code = (
        "from bisim.extract import parse_signature; from bisim.probes import generate_type_probes; import json;"
        "s=parse_signature('def f(xs: list[int], k: str) -> int');"
        "print(json.dumps([p.id for p in generate_type_probes(s)]))"
    )
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    b = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert json.loads(a) == json.loads(b) and len(json.loads(a)) >= 40


def test_different_signatures_different_probes():
    a = parse_signature("def f(n: int) -> int")
    b = parse_signature("def f(n: int) -> str")
    assert ids(a) != ids(b)


def test_boundaries_present():
    s = parse_signature("def f(n: int, x: float, s: str, xs: list[int]) -> int")
    args = [p.args for p in generate_type_probes(s)]
    assert any(a[0] == 0 for a in args) and any(a[0] == -1 for a in args)
    assert any(isinstance(a[1], float) and math.isnan(a[1]) for a in args)
    assert any(a[2] == "" for a in args) and any(a[2] == "héllo" for a in args)
    assert any(a[3] == [] for a in args)


def test_unique_ids_and_kind():
    s = parse_signature("def f(n: int) -> int")
    ps = generate_type_probes(s)
    assert len({p.id for p in ps}) == len(ps) and all(p.kind == "type" for p in ps)


def test_probe_id_is_canonical_hash():
    p = Probe((1, "a"))
    from bisim.canon import canon_json, sha256_hex

    assert p.id == sha256_hex(canon_json([1, "a"]))


@pytest.mark.parametrize(
    "t",
    [
        "int", "float", "str", "bool", "bytes", "None",
        "list[int]", "List[int]", "tuple[int, str]", "tuple[int, ...]", "Tuple[int, ...]",
        "dict[str, int]", "Dict[str, int]", "set[int]", "frozenset[str]",
        "Optional[int]", "int | None", "Union[int, str]", "Literal['a', 'b']", "Literal[1, 2]",
        "Any", "Sequence[float]", "Mapping[str, int]", "Iterable[str]", "list[list[int]]",
        "dict[str, list[int]]", "typing.List[int]", "list[Optional[int]]",
    ],
)
def test_supported_types(t):
    st = strategy_for(t)
    assert st.boundaries
    import random

    r = random.Random(0)
    for _ in range(20):
        canon(st.draw(r))  # must be canonicalizable


def test_unsupported_type():
    with pytest.raises(Unsupported):
        strategy_for("Callable[[int], int]")
    with pytest.raises(Unsupported):
        strategy_for("Foo")
    with pytest.raises(Unsupported):
        strategy_for("list[Foo]")


def test_dataclass_resolver():
    st = strategy_for("Point", resolver={"Point": [("x", "int"), ("y", "int")]})
    c = canon(st.boundaries[0])
    assert c[0] == "D" and c[1] == "Point"
    s = parse_signature("def f(p: Point, k: int) -> int")
    ps = generate_type_probes(s, resolver={"Point": [("x", "int"), ("y", "int")]})
    assert len(ps) >= 40 and canon(ps[0].args[0])[0] == "D"


def test_version():
    assert PROBEGEN_VERSION == "v1"
