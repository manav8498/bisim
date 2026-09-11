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
    decisions: list[dict] = field(default_factory=list)  # {line, kind, body: [lines]} for if/while/for
    defaults: dict[str, str] = field(default_factory=dict)  # parameter name -> default expression source

    def signature_str(self) -> str:
        parts = [f"{n}: {t}" + (f" = {self.defaults[n]}" if n in self.defaults else "") for n, t in self.params]
        return "(" + ", ".join(parts) + f") -> {self.returns}"

    def required_count(self) -> int:
        """Number of leading parameters without a default."""
        return sum(1 for n, _ in self.params if n not in self.defaults)


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
    positional = a.posonlyargs + a.args
    for arg in positional:
        if arg.annotation is None:
            raise Unsupported(f"{node.name}: missing type hint for parameter '{arg.arg}'")
        params.append((arg.arg, ast.unparse(arg.annotation)))
    if node.returns is None:
        raise Unsupported(f"{node.name}: missing return type hint")
    defaults = {}
    for arg, d in zip(positional[len(positional) - len(a.defaults):], a.defaults):
        defaults[arg.arg] = ast.unparse(d)
    source = ast.get_source_segment(src, node) or ""
    return FunctionSpec(node.name, params, ast.unparse(node.returns), source, path, node.lineno, _executable_lines(node), _decisions(node), defaults)


def _decisions(node) -> list[dict]:
    """Decision points (if/elif/while/for) with the executable lines of their true-branch body."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, (ast.If, ast.While, ast.For)):
            body = sorted({st.lineno for b in sub.body for st in ast.walk(b) if isinstance(st, ast.stmt)})
            if not body or body[0] == sub.lineno:  # one-liner bodies are not measurable by line events
                continue
            kind = {ast.If: "if", ast.While: "while", ast.For: "for"}[type(sub)]
            out.append({"line": sub.lineno, "kind": kind, "body": body})
    return sorted(out, key=lambda d: d["line"])


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


# --- classes ----------------------------------------------------------------

@dataclass
class ClassSpec:
    name: str
    init_params: list[tuple[str, str]]
    methods: dict[str, FunctionSpec]  # params exclude ``self``
    is_dataclass: bool
    source: str
    module_path: str
    lineno: int
    lines: list[int] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    kinds: dict[str, str] = field(default_factory=dict)  # name -> "method" | "property" | "static" | "classmethod"
    init_defaults: dict[str, str] = field(default_factory=dict)

    def init_required_count(self) -> int:
        return sum(1 for n, _ in self.init_params if n not in self.init_defaults)

    def properties(self) -> list[str]:
        return sorted(m for m, k in self.kinds.items() if k == "property")

    def public_methods(self) -> list[str]:
        return sorted(m for m in self.methods if not m.startswith("_"))

    def signature_str(self) -> str:
        init = "(" + ", ".join(f"{n}: {t}" for n, t in self.init_params) + ")"
        return f"{self.name}{init}"


def parse_target(name: str) -> tuple[str | None, str | None]:
    """``"f"`` → (None, "f"); ``"C.m"`` → ("C", "m"); ``"C"`` → ("C", None). Classes are capitalized."""
    parts = name.split(".")
    if len(parts) == 1:
        n = parts[0]
        return (n, None) if n[:1].isupper() else (None, n)
    if len(parts) == 2:
        return parts[0], parts[1]
    raise Unsupported(f"target must be 'function', 'Class' or 'Class.method', got {name!r}")


def _is_dataclass_decorated(node: ast.ClassDef) -> bool:
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "dataclass":
            return True
    return False


_PROPERTY_DECOS = {"property", "cached_property"}


def _decorator_name(d) -> str:
    t = d.func if isinstance(d, ast.Call) else d
    if isinstance(t, ast.Name):
        return t.id
    if isinstance(t, ast.Attribute):
        return t.attr
    return ""


def _method_kind(node: ast.FunctionDef) -> str:
    names = {_decorator_name(d) for d in node.decorator_list}
    if names & _PROPERTY_DECOS:
        return "property"
    if "staticmethod" in names:
        return "static"
    if "classmethod" in names:
        return "classmethod"
    return "method"  # other decorators (lru_cache, custom) are called like ordinary methods


def _method_spec(node: ast.FunctionDef, src: str, path: str) -> tuple[FunctionSpec, str]:
    kind = _method_kind(node)
    a = node.args
    first = (a.posonlyargs + a.args)[0].arg if (a.posonlyargs + a.args) else None
    if kind == "static":
        spec = _spec_from_def(node, src, path)
    else:
        if first not in ("self", "cls"):
            raise Unsupported(f"{node.name}: first parameter must be self")
        spec = _spec_from_def(_without_self(node), src, path)
    if kind == "property" and spec.params:
        raise Unsupported(f"{node.name}: a property cannot take parameters")
    spec.name = node.name
    return spec, kind


def _without_self(node: ast.FunctionDef) -> ast.FunctionDef:
    import copy

    n = copy.copy(node)
    n.args = copy.copy(node.args)
    if n.args.posonlyargs:
        n.args.posonlyargs = n.args.posonlyargs[1:]
    else:
        n.args.args = n.args.args[1:]
    return n


def _class_spec(node: ast.ClassDef, src: str, path: str) -> ClassSpec:
    is_dc = _is_dataclass_decorated(node)
    init_params: list[tuple[str, str]] = []
    methods: dict[str, FunctionSpec] = {}
    kinds: dict[str, str] = {}
    init_node = None
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            init_node = item
        elif isinstance(item, ast.FunctionDef) and not (item.name.startswith("__") and item.name.endswith("__")):
            try:
                methods[item.name], kinds[item.name] = _method_spec(item, src, path)
            except Unsupported:
                continue
        elif isinstance(item, ast.AsyncFunctionDef):
            continue
    init_defaults: dict[str, str] = {}
    if is_dc:
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                t = ast.unparse(item.annotation)
                if t.startswith("ClassVar"):
                    continue
                init_params.append((item.target.id, t))
                if item.value is not None:
                    init_defaults[item.target.id] = ast.unparse(item.value)
    elif init_node is not None:
        a = init_node.args
        if a.vararg or a.kwarg or a.kwonlyargs:
            raise Unsupported(f"{node.name}.__init__: *args/**kwargs/keyword-only parameters are not supported")
        positional = (a.posonlyargs + a.args)[1:]
        for arg in positional:
            if arg.annotation is None:
                raise Unsupported(f"{node.name}.__init__: missing type hint for parameter '{arg.arg}'")
            init_params.append((arg.arg, ast.unparse(arg.annotation)))
        for arg, d in zip(positional[len(positional) - len(a.defaults):], a.defaults):
            init_defaults[arg.arg] = ast.unparse(d)
    lines = sorted({l for item in node.body if isinstance(item, ast.FunctionDef) for l in _executable_lines(item)})
    decisions = sorted((d for item in node.body if isinstance(item, ast.FunctionDef) for d in _decisions(item)), key=lambda d: d["line"])
    return ClassSpec(node.name, init_params, methods, is_dc, ast.get_source_segment(src, node) or "", path, node.lineno, lines, decisions, kinds, init_defaults)


def extract_class(path: str, name: str) -> ClassSpec:
    src = _read(path)
    for n in ast.parse(src).body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            return _class_spec(n, src, path)
    raise NotFound(f"no top-level class named '{name}' in {path}")


def extract_classes(path: str) -> list[ClassSpec]:
    src = _read(path)
    out = []
    for n in ast.parse(src).body:
        if isinstance(n, ast.ClassDef):
            try:
                out.append(_class_spec(n, src, path))
            except Unsupported:
                pass
    return out


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


def parse_class_stub(text: str) -> ClassSpec:
    """Build a ClassSpec from a stub like ``class C:\n    def __init__(self, n: int): ...\n    def m(self) -> int: ...``."""
    src = text.strip() + "\n"
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise Unsupported(f"cannot parse class stub: {e}")
    for n in tree.body:
        if isinstance(n, ast.ClassDef):
            c = _class_spec(n, src, "")
            c.source = ""
            return c
    raise Unsupported("class stub must contain a class definition")
