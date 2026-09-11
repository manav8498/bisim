"""Human-readable rendering of observations and arguments."""
from __future__ import annotations

from .canon import dumps, is_opaque, uncanon


def _val(v) -> str:
    return dumps(v) if is_opaque(v) else repr(uncanon(v))


def describe_obs(o: dict) -> str:
    if o.get("timeout"):
        return "⏱ timeout"
    if not o.get("ok"):
        return f"{'init ' if o.get('at') == 'init' else ''}raises {o.get('exc', '?')}"
    if "steps" in o:  # method / class sequence
        parts = [(_val(st["value"]) if st.get("ok") else f"raises {st.get('exc', '?')}") for st in o["steps"]]
        s = "; ".join(parts) if parts else "(no calls)"
        s += f"  → state {_val(o['state'])}"
    else:
        s = _val(o["value"])
    if o.get("out"):
        s += f"  [stdout: {o['out']!r}]"
    if o.get("effects"):
        s += "  [effects: " + ", ".join(_effect(e) for e in o["effects"][:6]) + (", …" if len(o["effects"]) > 6 else "") + "]"
    return s


def _effect(e: list) -> str:
    kind = e[0]
    if kind == "open":
        return f"open({e[2]}) {e[1]}"
    if kind == "fs":
        return f"fs {e[1]}={e[2][:8]}"
    if kind == "env":
        return f"env {e[1]}"
    if kind == "net":
        return f"net {':'.join(e[1:])}"
    if kind == "proc":
        return f"proc {e[1]}"
    return " ".join(e)


def _is_sequence_args(v) -> bool:
    return (isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], tuple) and isinstance(v[1], tuple)
            and all(isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str) and isinstance(c[1], tuple) for c in v[1]))


def fmt_args(canon_args: list) -> str:
    try:
        v = tuple(uncanon(canon_args))
    except ValueError:
        return dumps(canon_args)
    if _is_sequence_args(v):
        init, calls = v
        return "; ".join([f"new{_call(init)}"] + [f"{m}{_call(a)}" for m, a in calls])
    return repr(v)


def _call(args: tuple) -> str:
    return "(" + ", ".join(repr(a) for a in args) + ")"


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
