# bisim — behavior-addressed code

**Git addresses code by what it *says*. `bisim` addresses code by what it *does* — and a human signs the address.**

A function's **behavioral address** (`bsm1:…`) is a Merkle hash of its observed behavior on a
deterministic probe set: inputs generated from its type signature, plus inputs a human explicitly
*witnessed*. Two implementations with the same address behave identically on every one of those
inputs, no matter how differently they are written. When two addresses differ, `bisim` hands you the
exact input that proves it.

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
set — which is what witnesses are for. An address is always relative to its probe set; the manifest
records every probe and observation that went into it.

Enforced by the test suite: 15 behavior-preserving refactors (loop→comprehension, rename, early
return, helper extraction, statement reorder, class rewrites, …) yield **0 address changes**; 14 planted
mutants (`<`→`<=`, off-by-one, empty-input handling, exception type, float rounding, unicode length,
LIFO→FIFO, state mutated before raising, …) **each flip**, and the reported input reproduces the
difference on rerun.

## Install

```bash
pip install -e .            # stdlib only
pip install -e ".[llm]"     # + Anthropic SDK, for `mint` via API key
```

`mint` reaches a model through `ANTHROPIC_API_KEY` (SDK) or, if none is set, a locally installed
[Claude Code](https://claude.com/claude-code) (`claude -p`). Everything else runs offline.

## Commands

```
bisim hash    file.py:fn [--push]          address + manifest; --push stores it in the local registry
bisim diff    old.py:fn new.py:fn          behavioral diff; exit 0 same · 1 changed · 2 witnessed intent violated
bisim check   [--base HEAD]                every changed function in the git worktree vs base — the CI gate
bisim merge-check <base> --ours A --theirs B   three-way behavioral merge: conflicts git cannot see
bisim reach   file.py:fn                   model proposes inputs for lines no probe reaches; sandbox verifies; ledger keeps them
bisim mint    --intent … --sig … --out …   discover intent by asking only where candidates disagree
bisim witness add file.py:fn --input '[…]' (--expect V | --raises E | --run) [--note …]
bisim witness list file.py:fn
bisim lookup  bsm1:…                       find a witnessed implementation by behavior
```

Exit codes: `0` same · `1` changed on generated probes only · `2` changed on a witnessed input, or
signature changed · `3` refused (nondeterministic / unsupported, with the reason) · `4` error.

Run `examples/demo.sh` for the six-step walkthrough.

## Classes and stateful objects

Targets can be functions, methods, or whole classes:

```
bisim hash  m.py:Stack.push        # one call on a fresh instance: Stack(capacity).push(x)
bisim hash  m.py:Stack             # seeded call sequences over public methods, length 1–4
bisim witness add m.py:Stack --init '[2]' --calls '[["push",[1]],["push",[2]],["pop",[]]]' --run --note LIFO
```

Instances are built from the typed `__init__` (or dataclass fields) by the same seeded generator. An
observation is the return value (or exception) of each call **and the instance's state afterwards** —
a method's behavior is what it returns *and* what it does to `self`. So a mutant that mutates state
before raising, or a `pop` that quietly becomes FIFO, flips the address even when every single-call
result is unchanged. `check` and `merge-check` pair `Class` and `Class.method` targets automatically.

Not yet: `mint` and `reach` for classes; methods with decorators; properties; class methods.

## Behavioral merge conflicts

Git merges text. `bisim merge-check` asks whether two branches changed the same function's behavior
on the same inputs — and differently. `examples/merge_demo.sh`: Alice changes a fee helper at the top
of a file; Bob changes the caller at the bottom. Different hunks, so git merges cleanly:

```
$ bisim merge-check main --ours alice --theirs bob
fees.py:_fee   ours-only            behavior changed on 48 inputs
fees.py:total  CONFLICT             both changed 44 shared inputs; 44 disagree
    (0.0,): base=1.0  ours=3.0  theirs=2.0
    (1.0,): base=2.0  ours=4.0  theirs=3.0
$ git merge bob
Merge made by the 'ort' strategy.
$ bisim check --base main
fees.py:total  changed   44/48 inputs
    (0.0,): 1.0 -> 6.0          # neither author wrote this
```

Statuses: `conflict` · `independent` (both changed, on disjoint inputs) · `convergent` (both made
the same change) · `ours-only` / `theirs-only` · `delete-modify` · `added-both-same` /
`added-both-conflict` · `signature`. Exit 2 on any conflict.

## How the address is computed

```
P(f)      = P_type(signature) ∪ P_witness(f) ∪ P_suggested(f)     # deterministic, seeded, versioned
obs(f,p)  = canonical record of f(*p): {ok, value} | {exc} | {timeout}, run twice in two processes
address   = "bsm1:" + sha256("bisim/1" | probegen | sig_hash | merkle_root{ sha256(H(p) ‖ H(obs)) })
```

- **Probes** come from a seeded, versioned generator (`probegen/v1`) driven by the type hints:
  boundaries first (`0, -1, 2**63, nan, -0.0, "", "héllo", [], …`), then seeded draws. Same signature
  ⇒ same probes on any machine. Supports `int float str bool bytes None list tuple dict set frozenset
  Optional Union Literal Any @dataclass` and nested combinations.
- **Observations** are canonicalized (big ints as decimal strings, floats via `repr` with `nan`/`inf`/
  `-0.0` special-cased, sets sorted, dataclasses by field) so they hash identically everywhere.
  Exceptions are observed by type. `stdout` is captured and counts as behavior.
- **The sandbox** is a child process with the network blocked, a fresh temp cwd, `PYTHONHASHSEED=0`,
  a memory ceiling, and a per-probe timeout. Every probe runs in two separate processes; any
  disagreement means the function is **nondeterministic and refused** — no address is minted.
- **Witnesses** live in `.bisim/witness/<module>/<fn>.json`: the input, the expected observation, who
  recorded it, when, and what alternatives were rejected. They are probes with human authority: a
  diff that changes a witnessed input is an *intent violation* (exit 2), not merely a change (exit 1).

## Reaching what the generator can't

The seeded generator is blind to conditions like `7000 < x < 7100 and s.startswith("zz")`. `bisim hash`
says so (`coverage=57.1% … missed lines: 3, 5, 7`). `bisim reach` shows the model the function and the
unreached lines, asks for inputs that execute them, **runs each proposal under coverage** and keeps only
the ones that verifiably reach a missed line — as `suggested` probes in the ledger, so every later
`hash`/`diff`/`check` includes them:

```
$ bisim reach narrow.py:classify
classify: lines never reached before: 3, 5, 7
  + (7050, 'zzabc')
  + (-424242, 'a')
  + (0, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
  added 3 suggested probe(s), rejected 0 proposal(s)
  coverage now 100.0%
```

The model proposes; the sandbox decides. A proposal that doesn't reach a missed line, doesn't run, or
runs nondeterministically is rejected.

## How `mint` works

1. The model proposes *k* implementations that are individually reasonable but behaviorally different,
   and lists the ambiguities it exploited plus inputs likely to expose them.
2. Every candidate runs on the full probe set. Candidates that don't run, are nondeterministic, or
   raise `NameError`-class errors are dropped.
3. Candidates are grouped into behavior classes by address.
4. While more than one class survives, `bisim` picks the probe that splits the classes best
   (most distinct outputs, then highest entropy, then prefer human/model-chosen inputs over generated
   boundaries, then the simplest input) and asks. You choose an option, type the right output, mark the
   input invalid (a precondition), or stop. Each answer is saved before the next question.
5. If your answer matches no candidate, the model is asked again with the witnesses as hard
   constraints (up to two regeneration rounds). If nothing matches, `mint` stops — with your
   witnesses on disk.
6. **Confirmation.** Once one class survives, it has only been checked where *surviving* candidates
   disagreed. So `mint` shows you up to `--confirm N` (default 3) inputs that were contested by *any*
   candidate ever proposed — e.g. "on `('hello', -1)` the chosen implementation returns `''`; others
   raised `ValueError`" — and you approve or correct. A correction prunes the survivor and triggers
   regeneration. This step is what the original TiCoder protocol lacks, and it is where the benchmark
   below recovers its misses.
7. The survivor is written out, pushed to `.bisim/store/<address>/`, and the witnesses become a pytest
   file — plain asserts, no `bisim` dependency.

This is the TiCoder loop (Microsoft, 2022/2024) with a different output: not a ranked list you discard,
but a persistent, versioned, content-addressed record of intent that `diff` and `check` enforce forever.

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
  triggered. `mint` can only converge on behaviors the model proposes or that a witness forces it to
  propose. (One dev-set reference, `round_half`, was corrected during development: it used the
  `floor(x + 0.5)` idiom whose float artifact contradicts "nearest integer"; the fix is in the file.)

## Prior art, and what is actually new here

| Ingredient | Prior instances | What they stop short of |
|---|---|---|
| Content-addressed code | Unison, Aura VCS, Sem, protein-hash, Nulang, Rust `semantic-diff`, Dhall | All hash **syntax** (AST/MIR/normal forms). Aura: "Two nodes with different hashes… might be equivalent at runtime; Aura does not try to prove that." |
| Behavior fingerprints | behaviorprint (JS), fnprint (binaries), BeCoV (arXiv 2604.16933), hand-rolled snippets | Diff reports and archives, not identities. BeCoV observes whatever the existing test suite happens to hit; its authors list "robust fingerprinting" and "branching and merging" as future work. |
| Change-directed test generation | SemaDiff (2607.13111), DiffTestGen (2607.16024), Testora (2503.18597), differential fuzzing (2602.15761) | Per-PR, model-generated tests: nondeterministic, no stable identity across commits or machines, no human ledger. Strong evidence the *problem* is real (they expose behavioral differences in ~76–78% of PRs). |
| Interactive intent elicitation | TiCoder (MSR), CodeT/AlphaCode clustering | Ephemeral: nothing persists, nothing is versioned, nothing is reused. |
| "Version behavior" framing | Morph | Versions eval *scores* per commit; no function-level behavioral identity, no witnessing. |
| Intent tooling for agents | northstar, IIC, taskwitness, tink, witness | Intent is *written* (frozen requirements, hash-locked Given/When/Then), not *discovered* on discriminating inputs, and never becomes the code's identity. |

**The composition — a deterministic, signature-derived probe set that makes behavior a stable identity,
signed by human-witnessed discriminating inputs, used as the unit of diff, CI gating, and reuse — had
no prior instance in two sweeps of arXiv, GitHub, HN, PyPI and X through 2026-09-11.** Each ingredient
existed somewhere. The primitive did not. (Git was Merkle trees + diffs + DAGs; none were new either.)

## Limitations (v1, deliberate)

- Python ≥ 3.11 only; top-level functions and classes with complete type hints; positional arguments.
- Deterministic code. Nondeterminism is refused, not tolerated. File/network side effects are not
  modeled (network is blocked; the cwd is a scratch dir).
- Async, generators, `*args/**kwargs`, keyword-only parameters, decorated methods, and parameter types
  the generator can't construct (arbitrary non-dataclass classes, callables) are refused with a reason.
  A target with unsupported parameter types can still be addressed **witness-only** if its ledger has
  inputs.
- Registry is local (`.bisim/store`, git-committable). No SMT, no coverage-guided probe growth.
- Same address is evidence, not proof. Read the guarantee above.

## Roadmap

Done since the v1 spec: line coverage per address and growth-until-plateau in `diff`/`check` ·
`reach` · `merge-check` · confirmation phase in `mint` · evaluation harness · GitHub Action.

Done in 0.3.0: methods and classes via constructor + call-sequence probes, with state in observations.

Next: `mint`/`reach` for classes · effect ledgers (file/network calls as observations) · branch (not
just line) coverage · a shared registry so agents reuse witnessed implementations instead of
regenerating them.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q           # 166 tests, ~25 s, offline
python -m bisim.evalbench     # reproduce the evaluation from cached generations
bash examples/demo.sh
```

Design spec: `docs/superpowers/specs/2026-09-11-bisim-design.md`. Plan: `docs/superpowers/plans/2026-09-11-bisim.md`.

MIT.

## GitHub Action

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- uses: <your-org>/bisim@main        # this repo doubles as a composite action
  with:
    base: origin/${{ github.base_ref }}
    fail-on: witness                 # or "change" to block on any behavioral change
```

Every changed function is diffed against the base ref; the table lands in the job summary. Probe sets
widen automatically until each function's lines have all executed at least once (or coverage plateaus),
and any lines that never ran are named in the output.
