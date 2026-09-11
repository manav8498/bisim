"""A probe target: a function, a single method on a fresh instance, or a class under call sequences."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .address import Manifest, build_manifest_for, coverage_summary, sig_hash
from .extract import ClassSpec, FunctionSpec, Unsupported, extract_class, extract_function, parse_target
from .probes import DEFAULT_COUNT, Probe, class_sig_hash, dataclass_type, generate_method_probes, generate_sequence_probes, generate_type_probes
from .sandbox import DEFAULT_TIMEOUT, introspect, observe_cov
from .witness import ledger_probes, load_ledger


@dataclass
class Target:
    kind: str  # "function" | "method" | "class"
    path: str
    name: str  # "f" | "Stack.push" | "Stack"
    spec: FunctionSpec | ClassSpec
    method: str | None = None

    # --- construction -------------------------------------------------------
    @staticmethod
    def load(path: str, name: str) -> "Target":
        cls_name, meth = parse_target(name)
        if cls_name is None:
            return Target("function", path, meth, extract_function(path, meth))
        cls = extract_class(path, cls_name)
        if meth is None:
            return Target("class", path, cls_name, cls)
        if meth not in cls.methods:
            raise Unsupported(f"{cls_name}.{meth}: not a supported method (needs self + fully typed parameters, no decorators)")
        return Target("method", path, f"{cls_name}.{meth}", cls, meth)

    # --- identity -------------------------------------------------------------
    @property
    def class_name(self) -> str | None:
        return None if self.kind == "function" else self.spec.name

    def sig(self) -> str:
        if self.kind == "function":
            return sig_hash(self.spec)
        return class_sig_hash(self.spec, self.method)

    def signature_str(self) -> str:
        if self.kind == "function":
            return f"{self.spec.name}{self.spec.signature_str()}"
        if self.kind == "method":
            m = self.spec.methods[self.method]
            return f"{self.spec.signature_str()}.{self.method}{m.signature_str()}"
        return f"{self.spec.signature_str()} with {', '.join(self.spec.public_methods())}"

    def lines(self) -> list[int]:
        if self.kind == "method":
            return list(self.spec.methods[self.method].lines)
        return list(self.spec.lines)

    def describe(self) -> dict:
        if self.kind == "function":
            return {"kind": "function", "name": self.spec.name, "params": [list(p) for p in self.spec.params], "returns": self.spec.returns}
        d = {"kind": self.kind, "name": self.name, "class": self.spec.name, "params": [list(p) for p in self.spec.init_params]}
        if self.kind == "method":
            m = self.spec.methods[self.method]
            d["method_params"] = [list(p) for p in m.params]
            d["returns"] = m.returns
        else:
            d["methods"] = self.spec.public_methods()
        return d

    # --- probes ---------------------------------------------------------------
    def _type_names(self) -> list[str]:
        import ast

        names = set()
        params = list(self.spec.params) if self.kind == "function" else list(self.spec.init_params)
        if self.kind == "method":
            params += self.spec.methods[self.method].params
        elif self.kind == "class":
            for m in self.spec.public_methods():
                params += self.spec.methods[m].params
        for _, t in params:
            try:
                for n in ast.walk(ast.parse(t, mode="eval")):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
            except SyntaxError:
                pass
        return sorted(names)

    def resolver(self) -> dict | None:
        if not os.path.exists(self.path):
            return None
        info = introspect(self.path, self._type_names())
        res = {k: v for k, v in info.items() if v}
        return res or None

    def type_probes(self, count: int = DEFAULT_COUNT, resolver=None) -> list[Probe]:
        if self.kind == "function":
            return generate_type_probes(self.spec, count, resolver)
        if self.kind == "method":
            return generate_method_probes(self.spec, self.method, count, resolver)
        return generate_sequence_probes(self.spec, count, resolver=resolver)

    def ledger_probes(self, root, resolver=None) -> list[Probe]:
        ledger = load_ledger(root, self.path, self.name)
        cr = (lambda q: dataclass_type(q, resolver[q.split(".")[-1]])) if resolver else None
        return ledger_probes(ledger, cr)

    def probes(self, root, count: int = DEFAULT_COUNT, resolver=None) -> tuple[list[Probe], list[str]]:
        """Ledger probes first (witness > suggested), then generated; deduped by id."""
        warnings: list[str] = []
        lp = self.ledger_probes(root, resolver)
        try:
            tp = self.type_probes(count, resolver)
        except Unsupported as e:
            if not lp:
                raise
            warnings.append(f"witness-only: {e}")
            tp = []
        seen = {p.id for p in lp}
        out = list(lp)
        for p in tp:
            if p.id not in seen:
                seen.add(p.id)
                out.append(p)
        return out, warnings

    # --- execution --------------------------------------------------------------
    def observe(self, probes: list[Probe], timeout: float = DEFAULT_TIMEOUT) -> tuple[list[dict], list[set[int]]]:
        fn = self.spec.name if self.kind == "function" else ""
        return observe_cov(self.path, fn, probes, timeout, class_name=self.class_name)

    def manifest(self, probes: list[Probe], obs: list[dict], cov: list[set[int]] | None = None) -> Manifest:
        summary = coverage_summary(self.lines(), cov) if cov is not None else None
        return build_manifest_for(self.describe(), self.sig(), probes, obs, summary)

    def source(self) -> str:
        return self.spec.source
