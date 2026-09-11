"""Canonical, seeded, type-directed probe generation (``probegen/v1``).

The probe set for a signature is a pure function of the signature text and the
generator version: same signature ⇒ same probes on any machine. Boundary values
come first, then seeded draws. Nothing here imports the target module.
"""
from __future__ import annotations

import ast
import dataclasses
import random
from dataclasses import dataclass

from .canon import canon_json, sha256_hex
from .extract import ClassSpec, FunctionSpec, Unsupported

PROBEGEN_VERSION = "v1"
MAX_DEPTH = 3
DEFAULT_COUNT = 48


def sig_hash(spec: FunctionSpec) -> str:
    """Hash of the parameter types and return type (names excluded)."""
    return sha256_hex(canon_json([t for _, t in spec.params] + [spec.returns]))


@dataclass(frozen=True)
class Probe:
    args: tuple
    kind: str = "type"  # "type" | "witness" | "suggested"

    @property
    def id(self) -> str:
        return sha256_hex(canon_json(list(self.args)))


# --- dataclass stand-ins -----------------------------------------------------
# The generator never imports the target module. For a dataclass parameter it
# builds a structurally identical stand-in whose __qualname__ matches, so the
# canonical form (and therefore the probe id) is the same as the real class.
_dc_registry: dict[str, type] = {}


def dataclass_type(qualname: str, fields: list[tuple[str, str]]) -> type:
    if qualname not in _dc_registry:
        cls = dataclasses.make_dataclass(qualname.split(".")[-1], [f for f, _ in fields])
        cls.__qualname__ = qualname
        _dc_registry[qualname] = cls
    return _dc_registry[qualname]


# --- boundary values ---------------------------------------------------------
INT_B = [0, 1, -1, 2, 10, 2**31 - 1, -(2**31), 2**63]
FLOAT_B = [0.0, -0.0, 1.0, -1.0, 0.5, 1e308, 1e-308, float("inf"), float("-inf"), float("nan")]
STR_B = ["", " ", "a", "hello world", "héllo", "日本語", "\n", "0"]
BYTES_B = [b"", b"\x00", b"abc"]
_STR_ALPHABET = "abcXYZ019 _-.,é日\n"


class TypeStrategy:
    def __init__(self, boundaries, draw):
        self.boundaries = list(boundaries)
        self._draw = draw

    def draw(self, rng: random.Random):
        return self._draw(rng)


def _str_draw(rng):
    return "".join(rng.choice(_STR_ALPHABET) for _ in range(rng.randint(0, 8)))


def _parse(t: str) -> ast.expr:
    try:
        return ast.parse(t, mode="eval").body
    except SyntaxError:
        raise Unsupported(f"cannot parse type '{t}'")


_ALIASES = {
    "List": "list", "Dict": "dict", "Set": "set", "FrozenSet": "frozenset", "Tuple": "tuple",
    "Sequence": "list", "MutableSequence": "list", "Iterable": "list", "Collection": "list",
    "Mapping": "dict", "MutableMapping": "dict", "AbstractSet": "set", "MutableSet": "set",
}


def _name(node) -> str:
    if isinstance(node, ast.Name):
        return _ALIASES.get(node.id, node.id)
    if isinstance(node, ast.Attribute):  # typing.List → List → list
        return _ALIASES.get(node.attr, node.attr)
    if isinstance(node, ast.Constant) and node.value is None:
        return "None"
    raise Unsupported(f"unsupported type expression '{ast.unparse(node)}'")


def _hashable(x) -> bool:
    try:
        hash(x)
        return True
    except TypeError:
        return False


def _dedupe(vals):
    seen, out = set(), []
    for v in vals:
        k = canon_json(v)
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def _union(parts):
    b = _dedupe([x for p in parts for x in p.boundaries])
    return TypeStrategy(b, lambda r: r.choice(parts).draw(r))


def strategy_for(type_str: str, resolver=None, depth: int = 0) -> TypeStrategy:
    """``resolver`` maps a dataclass name → ``[(field, type_str), ...]``."""
    return _strategy(_parse(type_str), resolver, depth)


def _strategy(node, resolver, depth) -> TypeStrategy:
    if depth > MAX_DEPTH:
        return TypeStrategy([None], lambda r: None)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _union([_strategy(node.left, resolver, depth), _strategy(node.right, resolver, depth)])
    if isinstance(node, ast.Subscript):
        base = _name(node.value)
        args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if base == "Optional":
            return _union([_strategy(args[0], resolver, depth), TypeStrategy([None], lambda r: None)])
        if base == "Union":
            return _union([_strategy(a, resolver, depth) for a in args])
        if base == "Literal":
            vals = [a.value for a in args if isinstance(a, ast.Constant)]
            if not vals:
                raise Unsupported(f"unsupported Literal '{ast.unparse(node)}'")
            return TypeStrategy(vals, lambda r, v=vals: r.choice(v))
        if base in ("list", "set", "frozenset"):
            inner = _strategy(args[0], resolver, depth + 1)
            ctor = {"list": list, "set": set, "frozenset": frozenset}[base]
            if ctor is not list:
                inner_b = [x for x in inner.boundaries if _hashable(x)]
                if not inner_b:
                    raise Unsupported(f"unhashable element type in '{ast.unparse(node)}'")
            else:
                inner_b = inner.boundaries
            b = [ctor([]), ctor([inner_b[0]])]
            if len(inner_b) > 1:
                b.append(ctor(inner_b[:2]))
            if ctor is list and len(inner_b) > 2:
                b.append(ctor(inner_b[:4]))

            def draw(r, inner=inner, ctor=ctor):
                items = [inner.draw(r) for _ in range(r.randint(0, 6))]
                return ctor(items if ctor is list else [x for x in items if _hashable(x)])

            return TypeStrategy(_dedupe(b), draw)
        if base == "tuple":
            if len(args) == 2 and isinstance(args[1], ast.Constant) and args[1].value is Ellipsis:
                inner = _strategy(args[0], resolver, depth + 1)
                return TypeStrategy(
                    _dedupe([(), (inner.boundaries[0],), tuple(inner.boundaries[:2])]),
                    lambda r, inner=inner: tuple(inner.draw(r) for _ in range(r.randint(0, 6))),
                )
            parts = [_strategy(a, resolver, depth + 1) for a in args]
            return TypeStrategy(
                [tuple(p.boundaries[0] for p in parts)] + ([tuple(p.boundaries[-1] for p in parts)] if parts else []),
                lambda r, parts=parts: tuple(p.draw(r) for p in parts),
            )
        if base == "dict":
            if len(args) != 2:
                raise Unsupported(f"dict needs two type arguments: '{ast.unparse(node)}'")
            k = _strategy(args[0], resolver, depth + 1)
            v = _strategy(args[1], resolver, depth + 1)
            kb = [x for x in k.boundaries if _hashable(x)]
            if not kb:
                raise Unsupported(f"unhashable key type in '{ast.unparse(node)}'")
            b = [{}, {kb[0]: v.boundaries[0]}]
            if len(kb) > 1 and len(v.boundaries) > 1:
                b.append({kb[0]: v.boundaries[0], kb[1]: v.boundaries[1]})

            def draw(r, k=k, v=v):
                out = {}
                for _ in range(r.randint(0, 6)):
                    key = k.draw(r)
                    if _hashable(key):
                        out[key] = v.draw(r)
                return out

            return TypeStrategy(b, draw)
        raise Unsupported(f"unsupported generic type '{ast.unparse(node)}'")

    n = _name(node)
    if n == "int":
        return TypeStrategy(
            INT_B, lambda r: r.choice([r.randint(-10, 10), r.randint(-1000, 1000), r.randint(-(2**40), 2**40)])
        )
    if n == "float":
        return TypeStrategy(
            FLOAT_B, lambda r: r.choice([r.uniform(-10, 10), r.uniform(-1e6, 1e6), float(r.randint(-5, 5))])
        )
    if n == "bool":
        return TypeStrategy([False, True], lambda r: r.random() < 0.5)
    if n == "str":
        return TypeStrategy(STR_B, _str_draw)
    if n == "bytes":
        return TypeStrategy(BYTES_B, lambda r: bytes(r.randint(0, 255) for _ in range(r.randint(0, 6))))
    if n == "None":
        return TypeStrategy([None], lambda r: None)
    if n == "Any":
        return _union([_strategy(_parse(t), resolver, depth) for t in ("int", "str", "None", "list[int]")])
    if resolver and n in resolver:
        fields = resolver[n]
        cls = dataclass_type(n, fields)
        parts = [(f, _strategy(_parse(t), resolver, depth + 1)) for f, t in fields]
        return TypeStrategy(
            [cls(**{f: s.boundaries[0] for f, s in parts})],
            lambda r, cls=cls, parts=parts: cls(**{f: s.draw(r) for f, s in parts}),
        )
    raise Unsupported(f"unsupported type '{ast.unparse(node)}'")


def generate_type_probes(spec: FunctionSpec, count: int = DEFAULT_COUNT, resolver=None) -> list[Probe]:
    strategies = [strategy_for(t, resolver) for _, t in spec.params]
    rng = random.Random(int(sig_hash(spec)[:16], 16))
    probes: list[Probe] = []
    seen: set[str] = set()

    def add(args):
        p = Probe(tuple(args), "type")
        if p.id not in seen:
            seen.add(p.id)
            probes.append(p)

    # Phase A: every boundary of every parameter appears at least once.
    max_b = max((len(s.boundaries) for s in strategies), default=0)
    for j in range(max_b):
        add([s.boundaries[j % len(s.boundaries)] for s in strategies])
    if not strategies:
        add([])
        return probes
    # Phase B: seeded mixtures of boundaries and draws until ``count``.
    pool = [s.boundaries + [s.draw(rng) for _ in range(8)] for s in strategies]
    guard = 0
    while len(probes) < count and guard < count * 20:
        guard += 1
        add([rng.choice(pool[i]) if rng.random() < 0.6 else strategies[i].draw(rng) for i in range(len(strategies))])
    return probes[:count]


# --- classes: instances, single method calls, call sequences ------------------

def class_sig_hash(cls: ClassSpec, method: str | None = None) -> str:
    """Identity of the interface being probed: constructor types plus the method's (or every public
    method's, by name) parameter and return types."""
    parts: list = [[t for _, t in cls.init_params]]
    names = [method] if method else cls.public_methods()
    for m in names:
        f = cls.methods[m]
        parts.append([m, cls.kinds.get(m, "method"), [t for _, t in f.params], f.returns])
    return sha256_hex(canon_json(parts))


def _rng_for(seed_text: str) -> random.Random:
    return random.Random(int(sha256_hex(seed_text)[:16], 16))


def _draw_args(strategies: list[TypeStrategy], rng: random.Random, boundary_index: int | None = None) -> tuple:
    if boundary_index is not None:
        return tuple(s.boundaries[boundary_index % len(s.boundaries)] for s in strategies)
    return tuple(rng.choice(s.boundaries) if rng.random() < 0.5 else s.draw(rng) for s in strategies)


def generate_method_probes(cls: ClassSpec, method: str, count: int = DEFAULT_COUNT, resolver=None) -> list[Probe]:
    """Probes of the form ``(init_args, ((method, args),))``: one call on a fresh instance."""
    if method not in cls.methods:
        raise Unsupported(f"{cls.name}.{method}: not a supported method")
    init_s = [strategy_for(t, resolver) for _, t in cls.init_params]
    meth_s = [strategy_for(t, resolver) for _, t in cls.methods[method].params]
    rng = _rng_for(class_sig_hash(cls, method) + "|method")
    probes, seen = [], set()

    def add(init, margs):
        p = Probe((tuple(init), ((method, tuple(margs)),)), "type")
        if p.id not in seen:
            seen.add(p.id)
            probes.append(p)

    max_b = max([len(s.boundaries) for s in init_s + meth_s], default=1)
    for j in range(max_b):
        add(_draw_args(init_s, rng, j), _draw_args(meth_s, rng, j))
    guard = 0
    while len(probes) < count and guard < count * 20:
        guard += 1
        add(_draw_args(init_s, rng), _draw_args(meth_s, rng))
    return probes[:count]


def generate_sequence_probes(cls: ClassSpec, count: int = DEFAULT_COUNT, max_len: int = 4, resolver=None) -> list[Probe]:
    """Probes of the form ``(init_args, ((m1, args), (m2, args), ...))`` over public methods."""
    names = cls.public_methods()
    if not names:
        raise Unsupported(f"{cls.name}: no supported public methods to probe")
    init_s = [strategy_for(t, resolver) for _, t in cls.init_params]
    meth_s = {m: [strategy_for(t, resolver) for _, t in cls.methods[m].params] for m in names}
    rng = _rng_for(class_sig_hash(cls) + "|sequence")
    probes, seen = [], set()

    def add(init, calls):
        p = Probe((tuple(init), tuple(calls)), "type")
        if p.id not in seen:
            seen.add(p.id)
            probes.append(p)

    # Phase A: every public method alone on boundary instances/args.
    for m in names:
        max_b = max([len(s.boundaries) for s in init_s + meth_s[m]], default=1)
        for j in range(min(max_b, 4)):
            add(_draw_args(init_s, rng, j), [(m, _draw_args(meth_s[m], rng, j))])
    # Phase B: seeded sequences.
    guard = 0
    while len(probes) < count and guard < count * 20:
        guard += 1
        length = rng.randint(1, max_len)
        calls = [(m, _draw_args(meth_s[m], rng)) for m in (rng.choice(names) for _ in range(length))]
        add(_draw_args(init_s, rng), calls)
    return probes[:count]
