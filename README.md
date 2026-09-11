# bisim — behavior-addressed code

[![ci](https://github.com/manav8498/bisim/actions/workflows/ci.yml/badge.svg)](https://github.com/manav8498/bisim/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/bisim.svg)](https://pypi.org/project/bisim/) [![Python](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue.svg)](#install) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Git addresses code by what it *says*. `bisim` addresses code by what it *does* — and a human signs the address.**

A function's (or class's) **behavioral address** (`bsm1:…`) is a Merkle hash of its observed behavior
on a deterministic probe set: inputs generated from its type signature, plus inputs a human explicitly
*witnessed*. Two implementations with the same address behave identically on every one of those
inputs, no matter how differently they are written. When two addresses differ, `bisim` hands you the
exact input that proves it. Every address carries its own coverage — and names what it could not verify.

`bisim mint` closes the loop on intent: it asks a model for several *deliberately different*
implementations of what you asked for, runs them all, and asks you only about the inputs where they
disagree. Each answer becomes a permanent witness. The surviving implementation's address is,
literally, a hash of what you approved.

```
$ bisim mint --intent "median of a list of numbers" --sig "def median(xs: list[float]) -> float" --out median.py

Input: ([1e+308, 1.7e+308],)   (6 behaviors still possible)
  [0] inf            (3 candidates)
  [1] 1.35e+308      (1 candidate)
  [2] 1.7e+308       (1 candidate)
  [3] 1e+308         (1 candidate)
> 0

Input: ([],)   (3 behaviors still possible)
  [0] raises ValueError    (1 candidate)
  [1] nan                  (1 candidate)
  [2] 0.0                  (1 candidate)
> 0

questions asked: 2   witnesses: 2
wrote median.py
wrote tests/test_median_witness.py
address bsm1:0ced364a0934a3b862454ebfbc7b5221499ed4aee0d6828ebaca9942fce4fcc1
```

Six behaviorally distinct candidates, two questions to converge (plus up to three confirmations — see
below), one address. (Real run, Claude Opus 5, 2026-09-11.)

## Why

In 2026 the bottleneck in software moved. Generating code is cheap; knowing that generated code does
what you *meant* is not. Natural-language → formal-spec translation is still only 24–35% semantically
correct in controlled studies, and "there is no oracle for specification correctness other than the
user." Meanwhile every content-addressed code system (Unison, Aura, Sem, Nulang) hashes *syntax* and
stops there — Aura's docs say it outright: "Reasoning about behavior is the job of tests."

The cost is measured: a Feb 2026 differential-fuzzing study of six LLMs found **19–35% of
LLM-generated refactorings are not functionally equivalent** to the original, and **~21% of those slip
past the existing test suites** ([arXiv 2602.15761](https://arxiv.org/abs/2602.15761)). Tests passing
is not the same as behavior preserved.

`bisim` makes behavior the identity, and makes the human the oracle on exactly — and only — the inputs
that matter.

## The guarantee, stated precisely

- **Different address ⇒ behavior provably differs.** The tool names a concrete input and both outputs.
- **Same address ⇒ equivalent on the probe set.** Not a proof of total equivalence.

The asymmetry is deliberate and it is the right one for a gate: `bisim` never fabricates a difference,
and every "changed" verdict comes with evidence. It can miss a difference that lives outside the probe
set — which is why every address reports its **line and branch coverage** and names the lines and
branches no probe reached, why `diff`/`check` widen the probe set until coverage plateaus, why `reach`
exists, and why witnesses exist. An address is always relative to its probe set *and its runtime*; the
manifest records both.

Enforced by the test suite: 15 behavior-preserving refactors (loop→comprehension, rename, early
return, helper extraction, statement reorder, class rewrites, …) yield **0 address changes**; 14 planted
mutants (`<`→`<=`, off-by-one, empty-input handling, exception type, float rounding, unicode length,
LIFO→FIFO, state mutated before raising, …) **each flip**, and the reported input reproduces the
difference on rerun. Across Python 3.11 → 3.14, 59 of 61 fixtures have byte-identical addresses; the
two exceptions are float sums, where **Python 3.12 changed `sum()` to compensated summation** — a real
behavioral difference in the runtime, which `bisim` reports rather than hides.

## Install

```bash
pip install bisim              # stdlib only; Python ≥ 3.11
pip install "bisim[llm]"       # + Anthropic SDK, for `mint`/`reach` via API key
bisim init                     # .bisim/{witness,store,config.json} in your project
bisim install-hook             # optional: pre-commit runs `bisim check`
```

`mint` and `reach` reach a model through `ANTHROPIC_API_KEY` (SDK) or, if none is set, a locally
installed [Claude Code](https://claude.com/claude-code) (`claude -p`). Everything else runs offline.

## Commands

```
bisim hash    file.py:target [--push]           address, coverage, manifest; --push stores it (local + shared registry if configured)
bisim diff    old.py:target new.py:target       behavioral diff; exit 0 same · 1 changed · 2 witnessed intent violated
bisim check   [--base HEAD]                     every changed target in the git worktree vs base — the CI gate
bisim merge-check <base> --ours A --theirs B    three-way behavioral merge: conflicts git cannot see
bisim mint    --intent … --sig … --out …        discover intent by asking only where candidates disagree
bisim reach   file.py:target                    model proposes inputs for unreached lines/branches; sandbox verifies; ledger keeps them
bisim witness add file.py:target --input '[…]' [--init '[…]'] [--calls '[…]'] (--expect V | --raises E | --run) [--note …]
bisim witness list file.py:target
bisim lookup  bsm1:… | --sig "def f(…) -> …"   find a witnessed implementation by behavior, or list implementations of an interface
bisim serve   [--dir …] [--port 8765]           run a shared registry
bisim init · bisim install-hook
```

A *target* is `f` (a function), `Class` (call sequences over its public methods), or `Class.method`.

Exit codes: `0` same · `1` changed on generated probes only · `2` changed on a witnessed input, or
signature changed · `3` refused (nondeterministic / unsupported, with the reason) · `4` error.

`examples/demo.sh` is the six-step walkthrough; `examples/merge_demo.sh` shows a clean git merge that
is a behavioral conflict.

## How the address is computed

```
P(f)      = P_type(signature) ∪ P_witness(f) ∪ P_suggested(f)     # deterministic, seeded, versioned
obs(f,p)  = canonical record of f(*p): {ok, value} | {exc} | {timeout}  (+ stdout, + effects, + state for objects)
            run twice in two processes; any disagreement ⇒ refused as nondeterministic
address   = "bsm1:" + sha256("bisim/1" | probegen | sig_hash | merkle_root{ sha256(H(p) ‖ H(obs)) })
```

- **Probes** come from a seeded, versioned generator (`probegen/v1`) driven by the type hints:
  boundaries first (`0, -1, 2**63, nan, -0.0, "", "héllo", [], …`), then seeded draws. Same signature
  ⇒ same probes on any machine. Supports `int float str bool bytes None list tuple dict set frozenset
  Optional Union Literal Any @dataclass` and nested combinations. For classes: constructor arguments
  from the typed `__init__` or dataclass fields, and call sequences of length 1–4 over public methods.
- **Observations** are canonicalized (big ints as decimal strings, floats via `repr` with `nan`/`inf`/
  `-0.0` special-cased, sets sorted, dataclasses by field, other objects by public attributes) so they
  hash identically everywhere. Exceptions are observed by type. `stdout` counts as behavior.
- **Effects** count as behavior (see below). **State** counts as behavior for objects (see below).
- **The sandbox** is a child process per run with a fresh scratch directory per probe, the network and
  subprocesses blocked, `PYTHONHASHSEED=0`, a memory ceiling, and a per-probe timeout.
- **Coverage** (diagnostic, never hashed): executed lines and taken branches (`if`/`elif`/`while`/`for`,
  both outcomes) of the target, per address. `diff`/`check` grow the generated probe set 48 → 96 → … → 480
  until nothing is unreached or coverage stops improving, and warn about what is still unreached.
- **Witnesses** live in `.bisim/witness/<module>/<target>.json`: the input, the expected observation,
  who recorded it, when, and what alternatives were rejected. They are probes with human authority: a
  diff that changes a witnessed input is an *intent violation* (exit 2), not merely a change (exit 1).

## Side effects are observed, not forbidden

What a function *tries* to do is part of the observation — an **effect ledger** appended only when
non-empty, so pure functions' addresses are unchanged:

| effect | recorded as |
|---|---|
| `open(path, mode)` | `["open", path, mode]` (paths inside the scratch dir are relative) |
| files left in the scratch dir | `["fs", path, sha256]` — content-addressed, sorted |
| `os.environ[...]` / `os.getenv` / `in os.environ` | `["env", NAME]` |
| socket / `create_connection` / DNS | `["net", host, port]` — then `PermissionError` |
| `subprocess` / `os.system` | `["proc", argv0]` — then `PermissionError` |

```
$ bisim diff report_v1.py:save_report report_v2.py:save_report
CHANGED   changed on 48 of 48 inputs
  ('', [])   0  [effects: open(w) .txt, fs .txt=e3b0c442]   0  [effects: open(w) .txt, env REPORT_AUDIT, fs .txt=e3b0c442]
```

Same return value on every input; the new version reads an environment variable. That is a behavioral
change, and the address says so.

## Classes and stateful objects

```
bisim hash  m.py:Stack.push        # one call on a fresh instance: Stack(capacity).push(x)
bisim hash  m.py:Stack             # seeded call sequences over public methods, length 1–4
bisim witness add m.py:Stack --init '[2]' --calls '[["push",[1]],["push",[2]],["pop",[]]]' --run --note LIFO
```

An observation is the return value (or exception) of each call **and the instance's observable state
afterwards** — dataclass fields, or public attributes; private `_names` are implementation detail and
never affect an address. Sequences keep going after an exception, the way real callers do. Properties
and `cached_property` are read, `staticmethod`/`classmethod` are called, other decorators are called
through. So a mutant that mutates state before raising, or a `pop` that quietly becomes FIFO, flips the
address even when every single-call result is unchanged. `check`, `merge-check`, `reach` and `mint` all
take class targets; `mint --sig-file stub.py` takes a class stub and emits sequence tests.

```
$ bisim mint --intent "a rate limiter that allows up to limit calls until reset()" --sig-file stub.py --out rl.py
Input: new(-2147483648); remaining()   (6 behaviors still possible)
  [0] init raises ValueError    (3 candidates)
  [1] -2147483648               (1 candidate)
  [2] -1                        (1 candidate)
  [3] 0                         (1 candidate)
…
questions asked: 5   witnesses: 5
```

## Behavioral merge conflicts

Git merges text. `bisim merge-check` asks whether two branches changed the same target's behavior on
the same inputs — and differently. Alice changes a fee helper at the top of a file; Bob changes the
caller at the bottom. Different hunks, so git merges cleanly:

```
$ bisim merge-check main --ours alice --theirs bob
fees.py:_fee   ours-only            behavior changed on 48 inputs
fees.py:total  CONFLICT             both changed 44 shared inputs; 44 disagree
    (0.0,): base=1.0  ours=3.0  theirs=2.0
$ git merge bob
Merge made by the 'ort' strategy.
$ bisim check --base main
fees.py:total  changed   (0.0,): 1.0 -> 6.0          # neither author wrote this
```

Statuses: `conflict` · `independent` · `convergent` · `ours-only` / `theirs-only` · `delete-modify` ·
`added-both-same` / `added-both-conflict` · `signature`. Exit 2 on any conflict.

## Reaching what the generator can't

The seeded generator is blind to conditions like `7000 < x < 7100 and s.startswith("zz")`. `bisim hash`
says so (`lines 57.1% … missed lines: 3, 5, 7`, `branches never taken: L2 true`). `bisim reach` shows
the model the target and the unreached lines/branches, asks for inputs that execute them, **runs each
proposal under coverage** and keeps only the ones that verifiably reach something new — as `suggested`
probes in the ledger, so every later `hash`/`diff`/`check` includes them. The model proposes; the
sandbox decides.

## How `mint` works

1. The model proposes *k* implementations that are individually reasonable but behaviorally different,
   and lists the ambiguities it exploited plus inputs likely to expose them. If a shared registry is
   configured, implementations of the same interface that someone already witnessed join the pool.
2. Every candidate runs on the full probe set. Candidates that don't run, are nondeterministic, or
   raise `NameError`-class errors are dropped.
3. Candidates are grouped into behavior classes by address.
4. While more than one class survives, `bisim` picks the probe that splits the classes best (most
   distinct outputs, then highest entropy, then human/model-chosen inputs over generated boundaries,
   then the simplest input) and asks. You choose an option, type the right output, mark the input
   invalid (a precondition), or stop. Each answer is saved before the next question.
5. If your answer matches no candidate, the model is asked again with the witnesses as hard
   constraints (up to two regeneration rounds).
6. **Confirmation.** Once one class survives, it has only been checked where *surviving* candidates
   disagreed. So `mint` shows you up to `--confirm N` (default 3) inputs that were contested by *any*
   candidate — covering different *kinds* of disagreement first (value-vs-value, value-vs-exception,
   exception-type-vs-exception-type), model-suggested inputs first, one per distinct set of dissenters —
   and you approve or correct. A correction prunes the survivor and triggers regeneration. This step is
   what the original TiCoder protocol lacks, and it is where the evaluation below recovers its misses.
7. The survivor is written out, pushed to the registry, and the witnesses become a pytest file — plain
   asserts, no `bisim` dependency.

## Shared registry

```
bisim serve --dir /srv/bisim --port 8765            # zero-dependency HTTP server
bisim init --registry http://registry.internal:8765 # or: export BISIM_REGISTRY=…
bisim hash f.py:f --push                            # local store + shared registry
bisim lookup --sig "def median(xs: list[float]) -> float"
interface 3f1c0e2a9b7d…  2 implementation(s):
  bsm1:0ced364a0934…  remote  witnesses=2  median of a list of numbers
```

The registry is content-addressed by behavior and indexed by interface hash, so an agent can ask "has
anyone already witnessed an implementation of this signature?" before generating a new one — and
`mint` does exactly that. No auth; put it behind whatever your team already uses.

## Evaluation

`python -m bisim.evalbench` runs `mint` against a simulated user who answers every question from a
hidden reference implementation (the protocol TiCoder used to evaluate at scale). Candidates come from
Claude Opus 5 via Claude Code; every generation is cached under `examples/bench/cache*/`, so the tables
below reproduce offline with no model access. "First-candidate correct" is the baseline of taking the
model's first implementation as-is; "mint correct" means the implementation `mint` chose is
behaviorally identical to the reference on the full probe set.

**Development set — 10 tasks (`examples/bench/tasks.json`).** The confirmation heuristic was tuned
against these, so treat this as in-sample:

| | first-candidate correct | mint correct | mean behavior classes | mean questions |
|---|---|---|---|---|
| dev (n=10) | 6/10 | **10/10** | 4.9 | 5.6 |

**Held-out set — 5 tasks (`examples/bench/heldout.json`)**, written after the protocol was frozen and
run once:

| | first-candidate correct | mint correct | mean behavior classes | mean questions |
|---|---|---|---|---|
| held-out (n=5) | 1/5 | **4/5** | 5.4 | 5.8 |

Per-task tables are in `examples/bench/results*.json`. Two things worth knowing:

- Six candidates for one plain-English intent split into **~5 distinct behaviors** on average. That is
  the intent gap, measured.
- The held-out miss (`capitalize_words`) shows the protocol's hard limit: the reference split words on
  single spaces only; *no* candidate ever behaved that way, and none of the confirmation inputs
  happened to contain a tab or newline, so nothing contradicted the survivor and no regeneration was
  triggered. `mint` can only converge on behaviors the model proposes, a witness forces it to propose,
  or a registry already holds. (One dev-set reference, `round_half`, was corrected during development:
  it used the `floor(x + 0.5)` idiom whose float artifact contradicts "nearest integer".)

## Prior art, and what is actually new here

| Ingredient | Prior instances | What they stop short of |
|---|---|---|
| Content-addressed code | Unison, Aura VCS, Sem, protein-hash, Nulang, Rust `semantic-diff`, Dhall | All hash **syntax** (AST/MIR/normal forms). Aura: "Two nodes with different hashes… might be equivalent at runtime; Aura does not try to prove that." |
| Behavior fingerprints | behaviorprint (JS), fnprint (binaries), BeCoV (arXiv 2604.16933), hand-rolled snippets | Diff reports and archives, not identities. BeCoV observes whatever the existing test suite happens to hit; its authors list "robust fingerprinting" and "branching and merging" as future work. |
| Change-directed test generation | SemaDiff (2607.13111), DiffTestGen (2607.16024), Testora (2503.18597), differential fuzzing (2602.15761) | Per-PR, model-generated tests: nondeterministic, no stable identity across commits or machines, no human ledger. Strong evidence the *problem* is real (they expose behavioral differences in ~76–78% of PRs). |
| Interactive intent elicitation | TiCoder (MSR), CodeT/AlphaCode clustering | Ephemeral: nothing persists, nothing is versioned, nothing is reused; no confirmation phase. |
| "Version behavior" framing | Morph | Versions eval *scores* per commit; no function-level behavioral identity, no witnessing. |
| Intent tooling for agents | northstar, IIC, taskwitness, tink, witness | Intent is *written* (frozen requirements, hash-locked Given/When/Then), not *discovered* on discriminating inputs, and never becomes the code's identity. |

**The composition — a deterministic, signature-derived probe set that makes behavior a stable identity,
signed by human-witnessed discriminating inputs, used as the unit of diff, CI gating, merge analysis,
and reuse — had no prior instance in two sweeps of arXiv, GitHub, HN, PyPI and X through 2026-09-11.**
Each ingredient existed somewhere. The primitive did not. (Git was Merkle trees + diffs + DAGs; none
were new either.)

## Limitations

- Python ≥ 3.11; top-level functions and classes with complete type hints; positional arguments.
- Deterministic code. Nondeterminism is refused, not tolerated. Network and subprocesses are blocked
  (the attempt is recorded).
- Async, generators, `*args/**kwargs`, keyword-only parameters, dunder methods, and parameter types the
  generator can't construct (arbitrary non-dataclass classes, callables) are refused with a reason. A
  target with unsupported parameter types can still be addressed **witness-only** if its ledger has
  inputs.
- Addresses are relative to a runtime: a Python release that changes numeric semantics changes them
  (see `sum()` above). The manifest records the runtime; `diff` warns when they differ.
- Same address is evidence, not proof. Read the guarantee above.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q           # 193 tests, ~35 s, offline; passes on 3.11, 3.12, 3.13, 3.14
python -m bisim.evalbench     # reproduce the evaluation from cached generations
bash examples/demo.sh; bash examples/merge_demo.sh
uv build                      # wheel + sdist
```

Design spec with amendments: `docs/superpowers/specs/2026-09-11-bisim-design.md`. Plan:
`docs/superpowers/plans/2026-09-11-bisim.md`. Changes: `CHANGELOG.md`. Contributing: `CONTRIBUTING.md`.

## GitHub Action

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- uses: manav8498/bisim@v1.0.0       # this repo doubles as a composite action
  with:
    base: origin/${{ github.base_ref }}
    fail-on: witness                 # or "change" to block on any behavioral change
```

MIT.
