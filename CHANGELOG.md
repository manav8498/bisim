# Changelog

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
