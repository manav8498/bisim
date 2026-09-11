"""``mint``: discover intent by asking about the inputs where candidates disagree.

1. A client proposes K deliberately different implementations of the intent.
2. All candidates run on the probe set (generated ∪ suggested ∪ witnessed).
3. Candidates are grouped into behavior classes by address.
4. While more than one class survives: pick the probe that splits the classes
   best, show the distinct outputs, record the human's answer as a witness,
   prune. Every answer is saved to the ledger immediately.
5. The surviving class's implementation is written out and pushed to the
   registry; witnesses become pytest tests.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Protocol

from .address import build_manifest, obs_hash
from .canon import canon, canon_json, dumps, parse_literal, uncanon
from .core import merge_probes
from .extract import FunctionSpec
from .fmt import describe_obs
from .probes import Probe, generate_type_probes
from .sandbox import DEFAULT_TIMEOUT, Nondeterministic, SandboxError, observe_source
from .store import push
from .witness import Ledger, Witness, add_witness, ledger_probes, load_ledger, save_ledger

KIND_PREF = {"type": 0, "suggested": 1, "witness": 2}
BROKEN_EXCS = {"NameError", "ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError", "UnboundLocalError"}


@dataclass
class Generation:
    candidates: list[str]
    notes: list[str] = field(default_factory=list)
    suggested_args: list[list] = field(default_factory=list)


class CandidateClient(Protocol):
    def generate(self, intent: str, spec: FunctionSpec, k: int, witnesses: list[Witness]) -> Generation: ...


@dataclass
class Option:
    obs: dict
    count: int
    example_index: int


@dataclass
class Question:
    probe: Probe
    options: list[Option]
    remaining_classes: int


@dataclass
class Answer:
    kind: str  # "choose" | "output" | "obs" | "exclude" | "quit"
    index: int = -1
    value: object = None


class Answerer(Protocol):
    def ask(self, q: Question) -> Answer: ...


class OracleAnswerer:
    """Simulated user: answers every question with what a reference implementation does.
    Used for offline evaluation, exactly as TiCoder benchmarks with a hidden reference."""

    def __init__(self, reference_source: str, func_name: str, timeout: float = DEFAULT_TIMEOUT):
        self.src, self.fn, self.timeout = reference_source, func_name, timeout

    def ask(self, q: Question) -> Answer:
        rec = observe_source(self.src, self.fn, [q.probe], self.timeout)[0]
        return Answer("obs", value=rec)


class ScriptedAnswerer:
    def __init__(self, answers):
        self.answers = list(answers)

    def ask(self, q: Question) -> Answer:
        return self.answers.pop(0) if self.answers else Answer("quit")


class ConsoleAnswerer:
    def ask(self, q: Question) -> Answer:
        print(f"\nInput: {tuple(q.probe.args)!r}   ({q.remaining_classes} behaviors still possible)")
        for i, o in enumerate(q.options):
            print(f"  [{i}] {describe_obs(o.obs)}    ({o.count} candidate{'s' if o.count != 1 else ''})")
        print("  o:<literal>  give the correct output    x  invalid input (precondition)    q  stop asking")
        while True:
            try:
                s = input("> ").strip()
            except EOFError:
                print("  (no more input — stopping)")
                return Answer("quit")
            if s == "q":
                return Answer("quit")
            if s == "x":
                return Answer("exclude")
            if s.startswith("o:"):
                try:
                    return Answer("output", value=parse_literal(s[2:]))
                except Exception as e:  # noqa: BLE001
                    print(f"  could not parse: {e}")
            elif s.isdigit() and int(s) < len(q.options):
                return Answer("choose", int(s))
            else:
                print("  choose an index, o:<literal>, x, or q")


def select_probe(probes: list[Probe], obs_by_cand: list[list[dict]], alive: list[int]) -> int | None:
    """Index of the probe that best separates the alive candidates, or None."""
    best, best_key = None, None
    n = len(alive)
    for j, p in enumerate(probes):
        groups: dict[str, int] = {}
        for c in alive:
            h = obs_hash(obs_by_cand[c][j])
            groups[h] = groups.get(h, 0) + 1
        if len(groups) < 2:
            continue
        ent = -sum((k / n) * math.log(k / n) for k in groups.values())
        # ties: prefer human/model-chosen inputs over generated boundaries, then the simplest input
        key = (len(groups), round(ent, 9), KIND_PREF[p.kind], -len(canon_json(list(p.args))))
        if best_key is None or key > best_key:
            best, best_key = j, key
    return best


def _validate_suggested(spec: FunctionSpec, raw: list) -> list[Probe]:
    out = []
    for args in raw or []:
        if not isinstance(args, list) or len(args) != len(spec.params):
            continue
        try:
            uncanon(canon(list(args)))
        except Exception:  # noqa: BLE001
            continue
        out.append(Probe(tuple(args), "suggested"))
    return out


def _observe_candidates(sources: list[str], spec: FunctionSpec, probes: list[Probe], timeout: float) -> tuple[list[str], list[list[dict]]]:
    keep, obs = [], []
    for src in sources:
        try:
            o = observe_source(src, spec.name, probes, timeout)
        except (Nondeterministic, SandboxError):
            continue
        if any((not r.get("ok", True)) and r.get("exc") in BROKEN_EXCS for r in o):
            continue
        keep.append(src)
        obs.append(o)
    return keep, obs


def _consistent(obs: list[dict], probes: list[Probe], ledger: Ledger) -> bool:
    exp = {dumps(w.args): obs_hash(w.expect) for w in ledger.witnesses}
    for p, o in zip(probes, obs):
        k = dumps(canon(list(p.args)))
        if k in exp and obs_hash(o) != exp[k]:
            return False
    return True


def _address_of(spec, probes, obs) -> str:
    return build_manifest(spec, probes, obs).address


@dataclass
class MintResult:
    source: str
    address: str
    ledger: Ledger
    questions_asked: int
    out_path: str
    test_path: str | None
    notes: list[str] = field(default_factory=list)


def mint(intent: str, spec: FunctionSpec, client: CandidateClient, answerer: Answerer, root, out_path: str,
         k: int = 6, max_questions: int = 8, timeout: float = DEFAULT_TIMEOUT, tests_dir: str | None = None) -> MintResult:
    out_path = os.path.abspath(out_path)
    spec.module_path = out_path
    ledger = load_ledger(root, out_path, spec.name, spec)
    ledger.signature = spec.signature_str()

    gen = client.generate(intent, spec, k, ledger.witnesses)
    type_probes = generate_type_probes(spec)
    suggested = _validate_suggested(spec, gen.suggested_args)
    for p in suggested:
        c = canon(list(p.args))
        if c not in ledger.suggested and all(dumps(w.args) != dumps(c) for w in ledger.witnesses):
            ledger.suggested.append(c)
    save_ledger(root, out_path, spec.name, ledger)
    probes = merge_probes(ledger_probes(ledger), suggested, type_probes)

    cands, obs_by_cand = _observe_candidates(gen.candidates, spec, probes, timeout)
    if not cands:
        raise SandboxError("no candidate could be executed")
    alive = [i for i in range(len(cands)) if _consistent(obs_by_cand[i], probes, ledger)]
    asked = 0
    regenerated = False

    while asked < max_questions and alive:
        classes: dict[str, list[int]] = {}
        for i in alive:
            classes.setdefault(_address_of(spec, probes, obs_by_cand[i]), []).append(i)
        if len(classes) <= 1:
            break
        j = select_probe(probes, obs_by_cand, alive)
        if j is None:
            break
        groups: dict[str, Option] = {}
        for i in alive:
            h = obs_hash(obs_by_cand[i][j])
            if h in groups:
                groups[h].count += 1
            else:
                groups[h] = Option(obs_by_cand[i][j], 1, i)
        options = sorted(groups.values(), key=lambda o: (-o.count, obs_hash(o.obs)))
        ans = answerer.ask(Question(probes[j], options, len(classes)))
        asked += 1
        if ans.kind == "quit":
            break
        if ans.kind == "exclude":
            ledger.excluded.append({"args": canon(list(probes[j].args)), "reason": "precondition"})
            save_ledger(root, out_path, spec.name, ledger)
            probes = probes[:j] + probes[j + 1:]
            obs_by_cand = [o[:j] + o[j + 1:] for o in obs_by_cand]
            continue
        if ans.kind == "choose":
            expect = options[ans.index].obs
        elif ans.kind == "obs":
            expect = ans.value
        else:
            expect = {"ok": True, "value": canon(ans.value)}
        add_witness(ledger, list(probes[j].args), expect, "", "mint")
        save_ledger(root, out_path, spec.name, ledger)
        probes[j] = Probe(probes[j].args, "witness")
        alive = [i for i in alive if obs_hash(obs_by_cand[i][j]) == obs_hash(expect)]
        if not alive and not regenerated:
            regenerated = True
            gen2 = client.generate(intent, spec, k, ledger.witnesses)
            more, more_obs = _observe_candidates(gen2.candidates, spec, probes, timeout)
            cands += more
            obs_by_cand += more_obs
            alive = [i for i in range(len(cands)) if _consistent(obs_by_cand[i], probes, ledger)]

    if not alive:
        raise SandboxError("no candidate matches the witnessed behavior; witnesses were saved to the ledger")

    chosen = alive[0]
    source = cands[chosen]
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(source.rstrip() + "\n")
    manifest = build_manifest(spec, probes, obs_by_cand[chosen])
    push(root, manifest, source, {"name": spec.name, "intent": intent, "witness_count": len(ledger.witnesses), "path": out_path})

    test_path = None
    if tests_dir and ledger.witnesses:
        os.makedirs(tests_dir, exist_ok=True)
        test_path = os.path.join(tests_dir, f"test_{spec.name}_witness.py")
        modname = os.path.splitext(os.path.basename(out_path))[0]
        import_stmt = f"import sys\nsys.path.insert(0, {os.path.dirname(out_path)!r})\nfrom {modname} import {spec.name}"
        with open(test_path, "w", encoding="utf-8") as fh:
            fh.write(emit_tests(ledger, spec, import_stmt))
    return MintResult(source, manifest.address, ledger, asked, out_path, test_path, gen.notes)


def pyrepr(v) -> str:
    """A valid Python literal for a canonicalizable value (handles nan/inf)."""
    if isinstance(v, float):
        if math.isnan(v):
            return "float('nan')"
        if math.isinf(v):
            return "float('inf')" if v > 0 else "float('-inf')"
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(pyrepr(x) for x in v) + "]"
    if isinstance(v, tuple):
        return "(" + ", ".join(pyrepr(x) for x in v) + ("," if len(v) == 1 else "") + ")"
    if isinstance(v, (set, frozenset)):
        return ("frozenset(" if isinstance(v, frozenset) else "") + "{" + ", ".join(pyrepr(x) for x in sorted(v, key=canon_json)) + "}" + (")" if isinstance(v, frozenset) else "") if v else ("frozenset()" if isinstance(v, frozenset) else "set()")
    if isinstance(v, dict):
        return "{" + ", ".join(f"{pyrepr(a)}: {pyrepr(b)}" for a, b in v.items()) + "}"
    return repr(v)


def emit_tests(ledger: Ledger, spec: FunctionSpec, import_stmt: str) -> str:
    lines = ["# generated by bisim mint from the witness ledger — each test is a human-approved behavior", import_stmt, "import math", "import pytest", "", ""]
    n = 0
    for w in ledger.witnesses:
        if w.expect.get("timeout"):
            continue
        args = ", ".join(pyrepr(a) for a in uncanon(w.args))
        call = f"{spec.name}({args})"
        note = f"  # {w.note}" if w.note else ""
        if w.expect.get("ok"):
            val = uncanon(w.expect["value"])
            if isinstance(val, float) and math.isnan(val):
                body = f"    assert math.isnan({call})"
            else:
                body = f"    assert {call} == {pyrepr(val)}"
        else:
            body = f"    with pytest.raises({w.expect['exc']}):\n        {call}"
        lines += [f"def test_witness_{n}():{note}", body, "", ""]
        n += 1
    return "\n".join(lines).rstrip() + "\n"
