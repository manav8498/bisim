# Changelog

## 1.1.0

Fixes for the problems found in the first outside review.

- Every input now starts from fresh module state. The target module and project-local modules are re-imported before each input, so module-level variables cannot leak between inputs and adding an input never changes another input's result.
- The second run now uses the reverse order. A result that depends on call order is refused, the same as a random one.
- Network, subprocess, file and environment recorders are installed before the target is imported, so import-time effects are observed and blocked.
- The address is computed on the 48 standard generated inputs only. Witnesses and suggested inputs go into a separate evidence hash (`bse1:`). Adding a witness no longer changes the address of unchanged code.
- Default parameter values are part of the interface. The generator leaves out trailing defaulted parameters in some inputs, so `f(x: int = 1)` and `f(x: int = 2)` now get different addresses.
- `BISIM_ISOLATION=process` (one child process per input) and `BISIM_RUNNER_WRAPPER` (prefix the child with a real sandbox such as bwrap or a container).
- `python -m bisim.mutbench`: mutation benchmark with an independent fuzzing oracle. Results are in the README.
- CI runs Python 3.14 as well. The package classifier is Beta, not Production.
- Documentation names SAM and QuietClash as prior work for merge checking and no longer claims that no other tool does behavioral merge detection.

Addresses of functions with default parameters, of targets that had witnesses, and of code with module-level state can differ from 1.0.x. All other addresses are unchanged.


## 1.0.1

- Documentation rewritten in plain language.
- Internal design notes removed from the repository.
- Output messages no longer use em dashes.
- No changes to how addresses are computed. Addresses from 1.0.0 are unchanged.

## 1.0.0

- Branch coverage reported next to line coverage. `diff`, `check` and `reach` treat an untaken branch like an unreached line.
- Properties, `cached_property`, static methods, class methods and decorated methods are probed. Objects are hashed by their public attributes, never by memory address.
- Shared registry: `bisim serve`, a client, `.bisim/config.json` and `BISIM_REGISTRY`, `hash --push` to both registries, `lookup` falls back to the shared registry, `lookup --sig` lists implementations of a signature, `mint` reuses stored implementations.
- `bisim init` and `bisim install-hook`. Default probe count and timeout can be set in `.bisim/config.json`.
- Packaging: license file, contributing guide, wheel and sdist. Tests pass on Python 3.11 to 3.14.

## 0.5.0

- `mint` for classes: `--sig` and `--sig-file` accept a class stub. Witnesses become sequence tests.
- `reach` for classes and methods.
- Object state is limited to public attributes. Call sequences continue after an exception.

## 0.4.0

- Effects recorded as part of the result: files opened, files written, environment variables read, blocked network and subprocess attempts.
- A fresh scratch directory for every input.

## 0.3.0

- Class and method targets. Instances are built from the typed constructor or dataclass fields. Class targets run call sequences of length 1 to 4.
- `witness add --init` and `--calls`. `check` and `merge-check` handle classes.

## 0.2.0

- Line coverage per address. `diff` and `check` add inputs until coverage stops improving.
- `reach`: model-suggested inputs for unreached lines, kept only when they verifiably reach them.
- `merge-check`: three-way behavior comparison for branches.
- `mint`: confirmation questions after convergence, up to two regeneration rounds, Claude Code as a fallback client.
- `python -m bisim.evalbench` with cached model output.
- GitHub Action and CI.

## 0.1.0

- `hash`, `diff`, `check`, `mint`, `witness`, `lookup`. Seeded input generator, sandbox with a two-run determinism check, Merkle addresses, local registry, and the fixture suite of refactor pairs and mutant pairs.
