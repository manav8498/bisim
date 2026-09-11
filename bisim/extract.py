"""Extract top-level, fully type-hinted functions from a Python source file."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field


class Unsupported(Exception):
    """The construct is outside what bisim v1 can address. The message says why."""


class NotFound(Exception):
    pass


@dataclass
class FunctionSpec:
    name: str
    params: list[tuple[str, str]]  # (name, type string as written)
    returns: str
    source: str
    module_path: str
    lineno: int
    lines: list[int] = field(default_factory=list)  # executable statement lines inside the body

    def signature_str(self) -> str:
        return "(" + ", ".join(f"{n}: {t}" for n, t in self.params) + f") -> {self.returns}"


def _spec_from_def(node, src: str, path: str) -> FunctionSpec:
    if isinstance(node, ast.AsyncFunctionDef):
        raise Unsupported(f"{node.name}: async functions are not supported")
    a = node.args
    if a.vararg or a.kwarg:
        raise Unsupported(f"{node.name}: *args/**kwargs are not supported")
    if a.kwonlyargs:
        raise Unsupported(f"{node.name}: keyword-only parameters are not supported")
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Yield, ast.YieldFrom)):
            raise Unsupported(f"{node.name}: generators are not supported")
    params: list[tuple[str, str]] = []
    for arg in a.posonlyargs + a.args:
        if arg.annotation is None:
            raise Unsupported(f"{node.name}: missing type hint for parameter '{arg.arg}'")
        params.append((arg.arg, ast.unparse(arg.annotation)))
    if node.returns is None:
        raise Unsupported(f"{node.name}: missing return type hint")
    source = ast.get_source_segment(src, node) or ""
    return FunctionSpec(node.name, params, ast.unparse(node.returns), source, path, node.lineno, _executable_lines(node))


def _executable_lines(node) -> list[int]:
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring
    lines = set()
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.stmt):
                lines.add(sub.lineno)
    return sorted(lines)


def executable_lines(spec: FunctionSpec) -> list[int]:
    return list(spec.lines)


def _top_level_defs(src: str):
    tree = ast.parse(src)
    return [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def extract_functions(path: str) -> list[FunctionSpec]:
    """All supported top-level functions in ``path``; unsupported ones are skipped."""
    src = _read(path)
    out = []
    for n in _top_level_defs(src):
        try:
            out.append(_spec_from_def(n, src, path))
        except Unsupported:
            pass
    return out


def extract_function(path: str, name: str) -> FunctionSpec:
    src = _read(path)
    for n in _top_level_defs(src):
        if n.name == name:
            return _spec_from_def(n, src, path)
    raise NotFound(f"no top-level function named '{name}' in {path}")


def parse_signature(sig: str) -> FunctionSpec:
    """Build a spec from a bare ``def f(x: int) -> int`` line (no body)."""
    text = sig.strip().rstrip(":") + ":\n    pass\n"
    try:
        node = _top_level_defs(text)[0]
    except (SyntaxError, IndexError):
        raise Unsupported(f"cannot parse signature: {sig!r}")
    s = _spec_from_def(node, text, "")
    s.source = ""
    return s
