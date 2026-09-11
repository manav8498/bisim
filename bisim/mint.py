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

from .address import behavior_key, build_manifest_for, obs_hash
from .canon import canon, canon_json, dumps, parse_literal, uncanon
from .core import merge_probes
from .extract import ClassSpec, FunctionSpec
from .fmt import describe_obs
from .probes import Probe, class_sig_hash, generate_sequence_probes, generate_type_probes, sig_hash
from .sandbox import DEFAULT_TIMEOUT, Nondeterministic, SandboxError
from .store import push
from .witness import Ledger, Witness, add_witness, ledger_probes, load_ledger, save_ledger

KIND_PREF = {"type": 0, "suggested": 1, "witness": 2}
BROKEN_EXCS = {"NameError", "ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError", "UnboundLocalError"}


def is_class_spec(spec) -> bool:
    return isinstance(spec, ClassSpec)


def observe_candidate(source: str, name: str, probes: list[Probe], timeout: float = DEFAULT_TIMEOUT, prelude: str = "") -> list[dict]:
    """Write ``source`` to a scratch module and observe target ``name`` (function, Class or Class.method) on ``probes``."""
    import tempfile

    from .target import Target

    with tempfile.TemporaryDirectory(prefix="bisim-src-") as d:
        path = os.path.join(d, "candidate.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(prelude + "\n" + source + "\n")
        return Target.load(path, name).observe(probes, timeout)[0]


def _type_probes(spec) -> list[Probe]:
    return generate_sequence_probes(spec) if is_class_spec(spec) else generate_type_probes(spec)


def _sig(spec) -> str:
    return class_sig_hash(spec) if is_class_spec(spec) else sig_hash(spec)


def _desc(spec) -> dict:
    if is_class_spec(spec):
        return {"kind": "class", "name": spec.name, "class": spec.name, "params": [list(p) for p in spec.init_params], "methods": spec.public_methods()}
    return {"kind": "function", "name": spec.name, "params": [list(p) for p in spec.params], "returns": spec.returns}


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
    confirm: bool = False  # True: the survivor's behavior on a once-contested input, shown for approval


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
        rec = observe_candidate(self.src, self.fn, [q.probe], self.timeout)[0]
        return Answer("obs", value=rec)


class ScriptedAnswerer:
    def __init__(self, answers):
        self.answers = list(answers)

    def ask(self, q: Question) -> Answer:
        return self.answers.pop(0) if self.answers else Answer("quit")


class ConsoleAnswerer:
    def ask(self, q: Question) -> Answer:
        if q.confirm:
            print(f"\nConfirm. Input: {tuple(q.probe.args)!r}   (the chosen implementation gives [0]; other candidates disagreed)")
        else:
            print(f"\nInput: {tuple(q.probe.args)!r}   ({q.remaining_classes} behaviors still possible)")
        for i, o in enumerate(q.options):
            print(f"  [{i}] {describe_obs(o.obs)}    ({o.count} candidate{'s' if o.count != 1 else ''})")
        print("  o:<literal>  give the correct output    x  invalid input (precondition)    q  stop asking")
        while True:
            try:
                s = input("> ").strip()
            except EOFError:
                print("  (no more input, stopping)")
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


def dissent_signature(obs_by_cand: list[list[dict]], survivor: int, j: int) -> frozenset[int]:
    """Which candidates disagree with the survivor on probe ``j``."""
    mine = obs_hash(obs_by_cand[survivor][j])
    return frozenset(i for i, o in enumerate(obs_by_cand) if obs_hash(o[j]) != mine)


def outcome_class(o: dict) -> str:
    if o.get("timeout"):
        return "timeout"
    return "value" if o.get("ok") else f"exc:{o.get('exc')}"


def disagreement_kind(obs_by_cand: list[list[dict]], j: int) -> frozenset[str]:
    """The set of outcome classes the pool produced on probe ``j`` (e.g. {value} or {value, exc:ValueError})."""
    return frozenset(outcome_class(o[j]) for o in obs_by_cand)


def select_confirmation(probes: list[Probe], obs_by_cand: list[list[dict]], survivor: int, skip: set[str],
                        used_sigs: list[frozenset[int]] = (), used_kinds: set[frozenset[str]] = frozenset()) -> int | None:
    """Index of the best probe to confirm the survivor's behavior on, or None.

    Preference order: a *kind* of disagreement not yet put to the user (value-vs-value, value-vs-
    exception, exception-type-vs-exception-type …) > model-suggested inputs > more dissenting
    candidates > simpler input. Probes whose exact set of dissenters was already asked are skipped.
    Together these make confirmations cover *different* ambiguities instead of re-asking one.
    """
    best, best_key = None, None
    for j, p in enumerate(probes):
        if p.id in skip or p.kind == "witness":
            continue
        sig = dissent_signature(obs_by_cand, survivor, j)
        if not sig or sig in used_sigs:
            continue
        kind = disagreement_kind(obs_by_cand, j)
        key = (kind not in used_kinds, KIND_PREF[p.kind], len(sig), -len(canon_json(list(p.args))))
        if best_key is None or key > best_key:
            best, best_key = j, key
    return best


def _validate_suggested(spec, raw: list) -> list[Probe]:
    out = []
    for entry in raw or []:
        if is_class_spec(spec):
            if not isinstance(entry, dict) or not isinstance(entry.get("init"), list) or len(entry["init"]) != len(spec.init_params):
                continue
            calls = entry.get("calls")
            if not isinstance(calls, list) or not calls:
                continue
            seq = []
            for c in calls:
                if not (isinstance(c, list) and len(c) == 2 and isinstance(c[0], str) and isinstance(c[1], list)):
                    seq = None
                    break
                if c[0] not in spec.methods or len(c[1]) != len(spec.methods[c[0]].params):
                    seq = None
                    break
                seq.append((c[0], tuple(c[1])))
            if seq is None:
                continue
            args = (tuple(entry["init"]), tuple(seq))
        else:
            if not isinstance(entry, list) or len(entry) != len(spec.params):
                continue
            args = tuple(entry)
        try:
            uncanon(canon(list(args)))
        except Exception:  # noqa: BLE001
            continue
        out.append(Probe(args, "suggested"))
    return out


def _broken(o: dict) -> bool:
    if (not o.get("ok", True)) and o.get("exc") in BROKEN_EXCS:
        return True
    return any((not st.get("ok", True)) and st.get("exc") in BROKEN_EXCS for st in o.get("steps", []))


def _observe_candidates(sources: list[str], spec, probes: list[Probe], timeout: float) -> tuple[list[str], list[list[dict]]]:
    keep, obs = [], []
    for src in sources:
        try:
            o = observe_candidate(src, spec.name, probes, timeout)
        except (Nondeterministic, SandboxError, Exception):  # noqa: BLE001 - unparseable / unsupported candidates are skipped
            continue
        if any(_broken(r) for r in o):
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
    """Grouping key for candidates: behavior on every probe run, not just the standard ones."""
    return behavior_key(probes, obs)


@dataclass
class MintResult:
    source: str
    address: str
    ledger: Ledger
    questions_asked: int
    out_path: str
    test_path: str | None
    notes: list[str] = field(default_factory=list)
    reused: int = 0  # candidates pulled from a shared registry
    chosen_from_registry: bool = False


def registry_candidates(registry, spec) -> list[str]:
    """Sources of implementations with the same interface already witnessed in a shared registry."""
    if registry is None:
        return []
    try:
        hits = registry.by_sig(_sig(spec))
    except Exception:  # noqa: BLE001 - an unreachable registry must not block minting
        return []
    out = []
    for h in sorted(hits, key=lambda h: -h.get("witness_count", 0)):
        try:
            obj = registry.lookup(h["address"])
        except Exception:  # noqa: BLE001
            continue
        if obj and obj["source"] not in out:
            out.append(obj["source"])
    return out


def mint(intent: str, spec: FunctionSpec, client: CandidateClient, answerer: Answerer, root, out_path: str,
         k: int = 6, max_questions: int = 8, timeout: float = DEFAULT_TIMEOUT, tests_dir: str | None = None,
         max_confirm: int = 3, max_regen: int = 2, registry=None) -> MintResult:
    out_path = os.path.abspath(out_path)
    spec.module_path = out_path
    ledger = load_ledger(root, out_path, spec.name, spec)
    ledger.signature = spec.signature_str()

    reused_sources = registry_candidates(registry, spec)
    gen = client.generate(intent, spec, k, ledger.witnesses)
    gen.candidates = reused_sources + [c for c in gen.candidates if c not in reused_sources]
    type_probes = _type_probes(spec)
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
    asked = confirmations = regens = 0
    declined: set[str] = set()  # confirmation probes the user quit on
    confirmed_sigs: list[frozenset[int]] = []  # disagreement patterns already put to the user

    def regenerate() -> list[int]:
        nonlocal cands, obs_by_cand
        gen2 = client.generate(intent, spec, k, ledger.witnesses)
        more, more_obs = _observe_candidates(gen2.candidates, spec, probes, timeout)
        cands += more
        obs_by_cand += more_obs
        return [i for i in range(len(cands)) if _consistent(obs_by_cand[i], probes, ledger)]

    while asked < max_questions:
        if not alive:
            if regens >= max_regen:
                break
            regens += 1
            alive = regenerate()
            if not alive:
                continue
        classes: dict[str, list[int]] = {}
        for i in alive:
            classes.setdefault(_address_of(spec, probes, obs_by_cand[i]), []).append(i)
        confirm = False
        if len(classes) > 1:
            j = select_probe(probes, obs_by_cand, alive)
            if j is None:
                break
            pool = alive
        else:
            if confirmations >= max_confirm:
                break
            used_kinds = {disagreement_kind(obs_by_cand, jj) for jj, pp in enumerate(probes) if pp.kind == "witness"}
            j = select_confirmation(probes, obs_by_cand, alive[0], declined, confirmed_sigs, used_kinds)
            if j is None:
                break
            confirmed_sigs.append(dissent_signature(obs_by_cand, alive[0], j))
            confirm = True
            pool = list(range(len(cands)))  # show every behavior ever proposed for this input
        groups: dict[str, Option] = {}
        for i in pool:
            h = obs_hash(obs_by_cand[i][j])
            if h in groups:
                groups[h].count += 1
            else:
                groups[h] = Option(obs_by_cand[i][j], 1, i)
        survivor_hash = obs_hash(obs_by_cand[alive[0]][j])
        options = sorted(groups.values(), key=lambda o: (obs_hash(o.obs) != survivor_hash, -o.count, obs_hash(o.obs)))
        ans = answerer.ask(Question(probes[j], options, len(classes), confirm))
        if ans.kind == "quit":
            if confirm:
                declined.add(probes[j].id)
                confirmations += 1
                continue
            break
        asked += 1
        if confirm:
            confirmations += 1
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
        rejected = [describe_obs(o.obs) for o in options if obs_hash(o.obs) != obs_hash(expect)]
        note = ("confirmed; rejected: " if confirm else "rejected: ") + " | ".join(rejected) if rejected else ("confirmed" if confirm else "")
        add_witness(ledger, list(probes[j].args), expect, note, "mint")
        save_ledger(root, out_path, spec.name, ledger)
        probes[j] = Probe(probes[j].args, "witness")
        alive = [i for i in alive if obs_hash(obs_by_cand[i][j]) == obs_hash(expect)]

    if not alive:
        raise SandboxError("no candidate matches the witnessed behavior; witnesses were saved to the ledger")

    chosen = alive[0]
    source = cands[chosen]
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(source.rstrip() + "\n")
    from .target import Target

    manifest = Target.load(out_path, spec.name).manifest(probes, obs_by_cand[chosen])
    push(root, manifest, source, {"name": spec.name, "intent": intent, "witness_count": len(ledger.witnesses), "path": out_path})

    test_path = None
    if tests_dir and ledger.witnesses:
        os.makedirs(tests_dir, exist_ok=True)
        test_path = os.path.join(tests_dir, f"test_{spec.name}_witness.py")
        modname = os.path.splitext(os.path.basename(out_path))[0]
        import_stmt = f"import sys\nsys.path.insert(0, {os.path.dirname(out_path)!r})\nfrom {modname} import {spec.name}"
        with open(test_path, "w", encoding="utf-8") as fh:
            fh.write(emit_tests(ledger, spec, import_stmt))
    return MintResult(source, manifest.address, ledger, asked, out_path, test_path, gen.notes,
                      len(reused_sources), source in reused_sources)


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


def _assert_line(call: str, expect: dict) -> str:
    if expect.get("ok"):
        val = uncanon(expect["value"])
        if isinstance(val, float) and math.isnan(val):
            return f"    assert math.isnan({call})"
        return f"    assert {call} == {pyrepr(val)}"
    return f"    with pytest.raises({expect['exc']}):\n        {call}"


def _sequence_test(spec: ClassSpec, args, expect: dict) -> str | None:
    init, calls = args
    ctor = f"{spec.name}({', '.join(pyrepr(a) for a in init)})"
    if not expect.get("ok"):  # constructor raised
        return f"    with pytest.raises({expect['exc']}):\n        {ctor}"
    steps = expect.get("steps", [])
    if not steps:
        return f"    obj = {ctor}"
    body = [f"    obj = {ctor}"]
    for (m, a), st in zip(calls, steps[:-1]):
        body.append(f"    obj.{m}({', '.join(pyrepr(x) for x in a)})")
    m, a = calls[len(steps) - 1]
    body.append(_assert_line(f"obj.{m}({', '.join(pyrepr(x) for x in a)})", steps[-1]))
    return "\n".join(body)


def emit_tests(ledger: Ledger, spec, import_stmt: str) -> str:
    lines = ["# generated by bisim mint from the witness file. Each test is a behavior a person approved.", import_stmt, "import math", "import pytest", "", ""]
    n = 0
    for w in ledger.witnesses:
        if w.expect.get("timeout"):
            continue
        args = uncanon(w.args)
        note = f"  # {w.note}" if w.note else ""
        if is_class_spec(spec):
            body = _sequence_test(spec, args, w.expect)
            if body is None:
                continue
        else:
            call = f"{spec.name}({', '.join(pyrepr(a) for a in args)})"
            body = _assert_line(call, w.expect)
        lines += [f"def test_witness_{n}():{note}", body, "", ""]
        n += 1
    return "\n".join(lines).rstrip() + "\n"
