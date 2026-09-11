# bisim

[![ci](https://github.com/manav8498/bisim/actions/workflows/ci.yml/badge.svg)](https://github.com/manav8498/bisim/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/bisim.svg)](https://pypi.org/project/bisim/) [![Python](https://img.shields.io/badge/python-3.11%20to%203.14-blue.svg)](#install) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

bisim gives a Python function or class an address based on what it does when you run it, not on how the code is written.

Git identifies code by its text. bisim identifies code by its behavior. It runs the code on a fixed set of inputs, records the results, and hashes them. Two implementations that behave the same on every input get the same address. Two that behave differently get different addresses, and bisim shows you an input where they differ.

You can use it to:

- check that a rewrite did not change behavior (`bisim diff`)
- block a commit or pull request that changes behavior (`bisim check`)
- find two branches that change the same behavior in different ways, even when git merges them cleanly (`bisim merge-check`)
- turn a plain English request into code by answering a few short questions (`bisim mint`)

The core has no dependencies outside the Python standard library. MIT license.

## Install

```bash
pip install bisim              # Python 3.11 or newer
pip install "bisim[llm]"       # adds the Anthropic SDK, needed for mint and reach with an API key
bisim init                     # creates .bisim/ in your project
bisim install-hook             # optional: run bisim check before every commit
```

`mint` and `reach` need a model. They use `ANTHROPIC_API_KEY` if it is set, or a locally installed [Claude Code](https://claude.com/claude-code) if not. Every other command works offline.

## A first look

`examples/median_v1.py` and `median_v3.py` both compute a median. They differ only on lists with an even number of elements: one returns the average of the two middle values, the other returns the lower one.

```
$ bisim diff examples/median_v1.py:median examples/median_v3.py:median
CHANGED  old=bsm1:b16f538238eb...  new=bsm1:e97f6cdcf74c...
  changed on 23 of 48 inputs (0 witnessed)
  input                 kind    old     new
  ([5.0, 4.0],)         type    4.5     4.0
  ([-1.0, -3.0],)       type    -2.0    -3.0
```

`median_v2.py` is the same as `median_v1.py` but written differently. It gets the same address:

```
$ bisim diff examples/median_v1.py:median examples/median_v2.py:median
SAME  bsm1:b16f538238eb431446a900a5c58a9d86fe8a6bd95ef116ba9c00246a521e91f3  (48 probes agree)
```

Run `bash examples/demo.sh` to see the full walkthrough.

## How it works

1. **Probes.** bisim reads the type hints of the function and generates inputs from them. The generator is seeded, so the same signature always produces the same inputs on any machine. It starts with edge cases (`0`, `-1`, very large numbers, `nan`, empty strings, unicode, empty lists) and then adds seeded random values. Parameters with default values are sometimes left out, so the defaults are exercised too. The identity always uses exactly 48 generated inputs.
2. **Observations.** Each input runs in a child process with a timeout, a fresh scratch directory, and the network blocked. Before every input, the target module and any project-local modules it imports are loaded again from scratch, so module-level state cannot leak from one input to the next. bisim records the return value, or the exception type, plus anything printed and any side effects. It then runs all inputs a second time, in a second process, in reverse order. If any result differs between the two runs, the function is refused. That catches randomness and it catches results that depend on the order of calls.
3. **Address.** The (input, result) pairs of the 48 standard inputs are hashed into a Merkle tree. The root, together with the signature and the generator version, becomes the address: `bsm1:` followed by 64 hex characters.

Two things are kept separate from the address on purpose:

- **Evidence.** Inputs a person approved (witnesses) and inputs a model found for unreached code (suggested) are hashed into a second value, `bse1:...`. Adding a witness to unchanged code does not change the code's address. It changes the evidence.
- **Coverage.** Which lines and branches the inputs reached. Reported, not hashed.

```
$ bisim hash examples/median_v1.py:median
bsm1:b16f538238eb431446a900a5c58a9d86fe8a6bd95ef116ba9c00246a521e91f3
  median(xs: list[float]) -> float
  probes=48 (type=48 witness=0 suggested=0)  python=3.13.5  probegen=v1
  coverage: lines 100.0% (7/7), branches 100.0% (4/4)
```

## What the address guarantees

- **Different addresses mean the code behaves differently.** bisim always shows a concrete input and both results.
- **The same address means the code behaves the same on every input bisim tried.** It is not a proof that the code is identical on all possible inputs.

This is the right direction for a gate. bisim never reports a difference that is not real. It can miss a difference on inputs it did not try. That is why every address reports its coverage and names the lines and branches nothing reached, why `diff` and `check` add more inputs (96, 192, up to 480) until coverage stops improving, and why witnesses and `reach` exist. The verdict of `diff` and `check` uses every input that was run, including witnesses and the extra inputs from growth. The address uses only the 48 standard ones.

The test suite checks this on 15 pairs of rewritten functions and classes (same address every time) and 14 pairs with a planted bug (different address every time, with an input that reproduces the bug). Across Python 3.11 to 3.14, 59 of 61 test fixtures get identical addresses. The two that differ add floats with `sum()`, and Python 3.12 changed how `sum()` rounds floats. That is a real difference in behavior, and bisim reports it.

`python -m bisim.mutbench` measures this at a larger scale. See "How much the probes catch" below.

## How much the probes catch

`python -m bisim.mutbench` takes every function in the test fixtures and the evaluation references (40 functions), generates mutants with small source changes (swap `<` for `<=`, add 1 to a constant, negate an `if`, drop a statement, and so on), and checks each mutant against an independent oracle: 2000 inputs from a different seed stream than the standard probes. The oracle decides whether the mutant really changes behavior. Then the benchmark records whether bisim catches it.

| | count |
|---|---|
| mutants generated | 290 |
| runnable (the rest were refused as nondeterministic or crashed) | 287 |
| mutants that change behavior, according to the oracle | 263 |
| caught by the 48 standard inputs | 255 (97.0%) |
| caught after `diff` grows the input set (up to 480) | 259 (98.5%) |
| reported as changed although the oracle saw no difference | 0 |

The four misses are all in a leap year calculation: to see them, an input needs a year divisible by 100 or by 400 together with month 2. Seeded random inputs do not find that. `reach` asks a model for such inputs and keeps the ones that work, and a witness fixes them for good.

The 24 mutants the oracle could not distinguish from the original are equivalent mutants (for example, dropping a statement that has no effect). bisim reported all 24 as unchanged, which is correct.

Results are in `examples/bench/mutants.json`. The corpus is small functions; results on large real-world code will be different, and the benchmark is written so you can point it at your own files with `--corpus`.

## Witnesses

A witness is an input that a person has looked at and approved, together with the expected result. Witnesses are stored in `.bisim/witness/` and are meant to be committed with the code.

```
$ bisim witness add examples/median_v1.py:median --input '[[1.0, 2.0, 3.0, 4.0]]' --expect 2.5 --note "even length: average of the middle two"
```

Witnesses are inputs with authority. If a change alters the result on a witnessed input, `diff` and `check` report it as an intent violation (exit code 2) instead of an ordinary change (exit code 1). Your CI can block on exit code 2 only, so refactors that change unwitnessed behavior still get through with a warning.

## Commands

```
bisim hash    file.py:target [--push]           print the address, coverage and probe counts; --push stores the code in the registry
bisim diff    old.py:target new.py:target       compare two implementations by behavior
bisim check   [--base HEAD]                     compare every changed function and class in the git working tree with a base commit
bisim merge-check <base> --ours A --theirs B    find behavior changes that two branches made to the same inputs
bisim mint    --intent "..." --sig "..." --out f.py   write code from a description by answering questions
bisim reach   file.py:target                    ask a model for inputs that reach lines and branches the probes missed
bisim witness add file.py:target --input '[...]' (--expect V | --raises E | --run) [--note "..."]
bisim witness list file.py:target
bisim lookup  bsm1:...  |  --sig "def f(...) -> ..."   find stored code by address, or list stored implementations of a signature
bisim serve   [--dir ...] [--port 8765]         run a shared registry
bisim init                                      create .bisim/ in a project
bisim install-hook                              install a git pre-commit hook that runs bisim check
```

A target is `f` for a function, `Stack` for a class, or `Stack.push` for one method.

Exit codes: `0` same, `1` changed, `2` changed on a witnessed input or the signature changed, `3` refused (the code could not be probed, with the reason), `4` error.

## Side effects, and what the sandbox is

Functions run in a scratch directory with the network and subprocesses blocked. What a function tries to do is recorded as part of its behavior:

| what the code does | what is recorded |
|---|---|
| opens a file | the path and mode |
| leaves files in the scratch directory | each path with a hash of its content |
| reads an environment variable | the variable name |
| tries to open a network connection | the host and port, then the call fails with `PermissionError` |
| tries to run a subprocess | the program name, then the call fails with `PermissionError` |

A function that starts reading an environment variable gets a new address even if it returns the same values:

```
$ bisim diff report_v1.py:save_report report_v2.py:save_report
CHANGED   changed on 48 of 48 inputs
  ('', [])   0  [effects: open(w) .txt, fs .txt=e3b0c442]   0  [effects: open(w) .txt, env REPORT_AUDIT, fs .txt=e3b0c442]
```

Pure functions have no effects recorded, so their addresses are unaffected by this feature.

The blocking is done by replacing Python functions (`socket`, `subprocess`, `open`, `os.environ`) inside the child process, and it is installed before the target module is imported. This is enough to keep results deterministic and to observe what code tries to do. It is not a security boundary. Code that wants to escape it can. If you run bisim on code you do not trust, for example pull requests from strangers, run the child process inside a real sandbox by setting `BISIM_RUNNER_WRAPPER` to a command prefix such as `bwrap --unshare-net --ro-bind / / --tmpfs /tmp --dev /dev`, or run bisim itself in a container. `BISIM_ISOLATION=process` starts one child process per input instead of one per run, which is slower and stricter.

## Classes

```
bisim hash m.py:Stack.push      # one call on a fresh instance: Stack(capacity).push(x)
bisim hash m.py:Stack           # sequences of one to four calls on a fresh instance
bisim witness add m.py:Stack --init '[2]' --calls '[["push",[1]],["push",[2]],["pop",[]]]' --run --note LIFO
```

bisim builds instances from the typed `__init__` or from dataclass fields. For a class target it runs sequences of calls to the public methods. Each observation records the result of every call and the public attributes of the object afterwards. Private attributes (names starting with `_`) are not included, so renaming internal fields does not change the address. Sequences continue after an exception, because real callers do too.

Properties are read, not called. Static methods and class methods are called. Methods with other decorators are called normally.

This catches bugs that single calls miss. A `pop` that quietly returns the first element instead of the last looks fine on a fresh stack. It fails on `push(1); push(2); pop()`.

## Merge conflicts git cannot see

Alice changes a helper at the top of a file. Bob changes the function that calls it at the bottom. The two edits are in different places, so git merges them without complaint. The merged function does something neither of them wrote.

```
$ bisim merge-check main --ours alice --theirs bob
fees.py:_fee   ours-only    behavior changed on 48 inputs
fees.py:total  CONFLICT     both changed 44 shared inputs; 44 disagree
    (0.0,): base=1.0  ours=3.0  theirs=2.0
$ git merge bob
Merge made by the 'ort' strategy.
$ bisim check --base main
fees.py:total  changed   (0.0,): 1.0 -> 6.0
```

Run `bash examples/merge_demo.sh` to reproduce this. The possible results are `conflict`, `independent` (both changed behavior, on different inputs), `convergent` (both made the same change), `ours-only`, `theirs-only`, `delete-modify`, `added-both-same`, `added-both-conflict`, and `signature`. Any conflict gives exit code 2.

## Reaching code the probes miss

The generator cannot guess an input for a condition like `7000 < x < 7100 and s.startswith("zz")`. `bisim hash` tells you which lines and branches were never reached. `bisim reach` shows the code and those lines to a model, asks for inputs that reach them, runs each suggestion, and keeps only the ones that actually reach something new. The kept inputs are stored as suggested probes and used by every later `hash`, `diff`, and `check`.

```
$ bisim reach narrow.py:classify
classify: lines never reached before: 3, 5, 7
  + (7050, 'zzabc')
  + (-424242, 'a')
  + (0, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
  added 3 suggested probe(s), rejected 0 proposal(s)
  coverage now 100.0%
```

## Writing code from a description with mint

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

This is a real run. A plain English description of a function is usually ambiguous. `mint` makes the ambiguity visible and lets you settle it with a few choices:

1. It asks a model for six implementations that are each reasonable but behave differently. If a shared registry is set up, implementations of the same signature that someone already approved are added to the pool.
2. It runs all of them on the probe inputs and groups them by address.
3. While more than one group is left, it picks the input that best separates the groups and asks you which result is right. Your answer is saved as a witness right away.
4. If no candidate matches your answer, it asks the model again with your answers as requirements.
5. When one group is left, it shows you up to three more inputs where other candidates had disagreed with the survivor, so you can confirm or correct them. If you correct one, the survivor is dropped and the model is asked again.
6. It writes the chosen code, stores it in the registry, and writes a pytest file from the witnesses.

For classes, pass a class stub with `--sig-file`. The questions become call sequences and the tests become sequence tests.

### How well mint works

`python -m bisim.evalbench` runs `mint` with a simulated user who answers every question from a hidden reference implementation. The model responses are cached in `examples/bench/`, so the numbers reproduce offline.

| task set | take the model's first answer | mint | average number of distinct behaviors among 6 candidates | average questions |
|---|---|---|---|---|
| 10 tasks used while developing the question strategy | 6 of 10 correct | 10 of 10 correct | 4.9 | 5.6 |
| 5 new tasks, run once after the strategy was fixed | 1 of 5 correct | 4 of 5 correct | 5.4 | 5.8 |

Six implementations of one plain English request split into about five different behaviors on average. That is the size of the problem.

The one miss on the new tasks shows the limit of the method. The reference implementation split words on single spaces only. None of the six candidates did that, and none of the confirmation inputs contained a tab or newline, so nothing contradicted the chosen candidate. `mint` can only arrive at behaviors that the model proposes, that a witness forces it to propose, or that the registry already holds.

## Shared registry

```
bisim serve --dir /srv/bisim --port 8765             # a small HTTP server, no dependencies
bisim init --registry http://registry.internal:8765  # or set BISIM_REGISTRY
bisim hash f.py:f --push                             # stores in the local and shared registry
bisim lookup --sig "def median(xs: list[float]) -> float"
```

The registry stores implementations by address and indexes them by signature. A tool or agent can ask whether someone has already approved an implementation of a signature before writing a new one. `mint` does this automatically. The server has no authentication. Run it inside your network.

## Similar tools and prior work

None of the ideas in bisim are new on their own. What bisim does is put them together in one tool for ordinary Python and git.

- **Content-addressed code.** Unison, Aura, Sem and others give code an address based on its syntax tree. Functions that behave the same but are written differently get different hashes there. bisim hashes behavior.
- **Behavior fingerprints.** behaviorprint records outputs on boundary inputs and diffs them. The BeCoV paper (2026) proposes keeping runtime behavior records next to git history for semantic diffing and lists behavior-aware merging as future work. In both, the fingerprint is a report, not an identity, and no person signs it.
- **Tests generated per change.** Testora, SemaDiff and DiffTestGen generate tests for a pull request to expose behavior changes. The tests are not deterministic and do not persist.
- **Asking the user to disambiguate.** TiCoder (Microsoft Research, 2022 and 2024) generates candidate programs and distinguishing tests and asks the user which is right. `mint` uses the same loop, adds the confirmation step, and keeps the answers as witnesses.
- **Semantic merge conflicts.** SAM (2024, Java) generates unit tests with EvoSuite and Randoop and runs them on base, both parents and the merge to find conflicts. QuietClash (2026, JavaScript) runs base, each branch and the merged result on synthesized inputs and reports a reproducing input, much like `bisim merge-check` does for Python.

So the fair description is: bisim is a behavior identity and intent layer for Python code in git, built from deterministic behavior fingerprints, inputs approved by a person, semantic diffing, behavior-based merge checking, and interactive code writing. The combination is what is new, not the parts.

## Limits

- Python 3.11 or newer. Top-level functions and classes with complete type hints. Positional arguments, with or without defaults.
- The code must be deterministic and must not depend on the order in which it is called. Code that fails either check is refused.
- Not supported: async functions, generators, `*args` and `**kwargs`, keyword-only parameters, dunder methods, and parameter types bisim cannot construct (callables, classes that are not dataclasses and have no typed `__init__`). Each case is refused with a message that says why. A function with an unsupported parameter type can still be addressed if its witness file provides inputs.
- Module-level state is reset between inputs for the target module and project-local modules only. State kept in third-party or standard library modules is not reset. The reversed second run will refuse the target if that state changes results.
- The sandbox is not a security boundary. See "Side effects, and what the sandbox is".
- Addresses depend on the Python version when the runtime changes numeric behavior, as with `sum()` in 3.12. The manifest records the version and `diff` warns when they differ.
- The same address is strong evidence, not proof. See "What the address guarantees".
- This is a young project. Version 1.x means the address format is stable, not that the tool has years of use behind it.

## GitHub Action

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- uses: manav8498/bisim@v1.1.0
  with:
    base: origin/${{ github.base_ref }}
    fail-on: witness      # fail only on witnessed changes; use "change" to fail on any behavior change
```

The results appear in the job summary.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q                  # about 210 tests, under a minute, no network needed
python -m bisim.evalbench            # rerun the mint evaluation from cached model output
python -m bisim.mutbench             # mutation benchmark, about 10 minutes
bash examples/demo.sh
bash examples/merge_demo.sh
```

`docs/design.md` explains the address format and the rules that keep it stable. `CHANGELOG.md` lists the changes in each version. `CONTRIBUTING.md` explains how to add fixtures and what must not change silently.

## License

MIT.
