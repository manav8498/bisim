"""Human-readable rendering of observations and arguments."""
from __future__ import annotations

from .canon import dumps, is_opaque, uncanon


def describe_obs(o: dict) -> str:
    if o.get("timeout"):
        return "⏱ timeout"
    if o.get("ok"):
        v = o["value"]
        s = dumps(v) if is_opaque(v) else repr(uncanon(v))
        if o.get("out"):
            s += f"  [stdout: {o['out']!r}]"
        return s
    return f"raises {o.get('exc', '?')}"


def fmt_args(canon_args: list) -> str:
    try:
        return repr(tuple(uncanon(canon_args)))
    except ValueError:
        return dumps(canon_args)


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
