"""Canonical serialization of Python values, and hashing helpers.

The canonical form is a small tagged tree that round-trips every value the probe
generator can produce, is JSON-serializable with sorted keys, and is identical
across machines and Python versions for the same value. Anything we cannot
represent losslessly is tagged ``["r", typename, repr]`` and flagged *opaque*.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import math


def _float_repr(x: float) -> str:
    if math.isnan(x):
        return "nan"
    if math.isinf(x):
        return "inf" if x > 0 else "-inf"
    return repr(x)


def canon(v):
    """Return the canonical tagged form of ``v``."""
    if v is None:
        return ["n"]
    if isinstance(v, bool):
        return ["B", 1 if v else 0]
    if isinstance(v, int):
        return ["i", str(v)]
    if isinstance(v, float):
        return ["f", _float_repr(v)]
    if isinstance(v, str):
        return ["s", v]
    if isinstance(v, (bytes, bytearray)):
        return ["b", bytes(v).hex()]
    if isinstance(v, list):
        return ["l", [canon(x) for x in v]]
    if isinstance(v, tuple):
        return ["t", [canon(x) for x in v]]
    if isinstance(v, (set, frozenset)):
        return ["S", sorted((canon(x) for x in v), key=dumps)]
    if isinstance(v, dict):
        return ["d", sorted(([canon(k), canon(x)] for k, x in v.items()), key=lambda kv: dumps(kv[0]))]
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        fields = sorted(dataclasses.fields(v), key=lambda f: f.name)
        return ["D", type(v).__qualname__, [[f.name, canon(getattr(v, f.name))] for f in fields]]
    return ["r", type(v).__qualname__, repr(v)]


def uncanon(t, resolver=None):
    """Inverse of :func:`canon`. ``resolver(qualname) -> class`` builds dataclasses."""
    tag = t[0]
    if tag == "n":
        return None
    if tag == "B":
        return bool(t[1])
    if tag == "i":
        return int(t[1])
    if tag == "f":
        return float(t[1])
    if tag == "s":
        return t[1]
    if tag == "b":
        return bytes.fromhex(t[1])
    if tag == "l":
        return [uncanon(x, resolver) for x in t[1]]
    if tag == "t":
        return tuple(uncanon(x, resolver) for x in t[1])
    if tag == "S":
        return set(uncanon(x, resolver) for x in t[1])
    if tag == "d":
        return {uncanon(k, resolver): uncanon(x, resolver) for k, x in t[1]}
    if tag == "D":
        if resolver is None:
            raise ValueError(f"cannot construct dataclass {t[1]} without a resolver")
        cls = resolver(t[1])
        return cls(**{name: uncanon(x, resolver) for name, x in t[2]})
    raise ValueError(f"cannot reconstruct opaque value: {t!r}")


def dumps(obj) -> str:
    """Canonical JSON text for an already-canonical structure."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canon_json(v) -> str:
    return dumps(canon(v))


def sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def is_opaque(t) -> bool:
    """True if any node in a canonical tree is an ``["r", ...]`` (repr-only) node."""
    if not isinstance(t, list) or not t:
        return False
    if t[0] == "r":
        return True
    stack = list(t[1:])
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            if x and x[0] == "r" and len(x) == 3 and isinstance(x[1], str):
                return True
            stack.extend(x)
    return False


def parse_literal(text: str):
    """Parse a CLI-supplied value: JSON first, then a Python literal."""
    try:
        return json.loads(text)
    except Exception:
        return ast.literal_eval(text)
