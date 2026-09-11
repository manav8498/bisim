# Changelog

## 1.0.0 — 2026-09-11

- Branch (decision) coverage per address alongside line coverage; `diff`/`check` growth and `reach`
  consider untaken branches.
- Properties, `cached_property`, `staticmethod`, `classmethod`, and otherwise-decorated methods are
  probed; arbitrary objects canonicalize structurally by public attributes (never by memory address).
- Shared registry: `bisim serve` (stdlib HTTP), `RemoteStore` client, `.bisim/config.json` /
  `$BISIM_REGISTRY` / `--registry`, `hash --push` to both stores, `lookup` falls back to remote,
  `lookup --sig` lists implementations of an interface, `mint` pulls witnessed implementations of the
  same interface into its candidate pool.
- `bisim init`, `bisim install-hook`; `count`/`timeout` defaults from config.
- Packaging: LICENSE (MIT), CONTRIBUTING, full metadata, wheel/sdist build; test suite passes on
  Python 3.11–3.14; cross-version address determinism 59/61 (the 2 differences are Python 3.12's
  compensated `sum()`).

## 0.5.0 — 2026-09-11

- `mint` for classes: `--sig`/`--sig-file` accept a class stub; candidates are whole classes probed
  by call sequences; suggested inputs are `{"init", "calls"}`; witnesses become sequence tests.
- `reach` for `Class` and `Class.method` targets.
- Observable state is public attributes only (dataclass fields, or names not starting with `_`);
  call sequences continue after an exception.

## 0.4.0 — 2026-09-11

- Effect ledger: `open`, files left in the scratch cwd (content-hashed), `os.environ` reads, and
  blocked network/subprocess attempts are recorded in the observation (`effects`, only when non-empty).
  Pure functions' addresses are unchanged.
- Fresh scratch directory per probe (no state leaks between probes).
- Clearer message when the sandbox child is killed by the time/memory budget.

## 0.3.0 — 2026-09-11

- Targets: `path.py:Class.method` (one call on a fresh instance) and `path.py:Class` (seeded call
  sequences over public methods, length 1–4). Instances built from typed `__init__` or dataclass
  fields. Observations include the instance state after the call(s).
- `witness add --init/--calls` for method and class targets; `check` and `merge-check` pair
  `Class` and `Class.method` targets.
- Negative-control suite extended with 3 class refactor pairs and 3 class mutants (incl. a mutant
  that only differs in state).
- Internal: `Target` adapter; `hash_target`/`diff_targets` generalize `hash_function`/`diff_functions`.

## 0.2.0 — 2026-09-11

- `hash` reports line coverage of the probe set and names unreached lines; `diff`/`check` widen the
  generated probe set (48 → 96 → … → 480) until every line has executed or coverage plateaus.
- `reach`: model-proposed, sandbox-verified inputs for unreached lines, stored as suggested probes.
- `merge-check`: three-way behavioral merge analysis (conflict / independent / convergent / …).
- `mint`: confirmation phase on once-contested inputs (kind-aware, one per distinct disagreement),
  up to two regeneration rounds, `--confirm N`; Claude Code (`claude -p`) client fallback; `--client`.
- `python -m bisim.evalbench`: reproducible evaluation with cached generations (dev 10/10, held-out 4/5).
- Composite GitHub Action (`action.yml`) and CI matrix.
- Witness ledger: readable layout, `display` strings, rejected alternatives recorded in notes.

## 0.1.0 — 2026-09-11

- `hash`, `diff`, `check`, `mint`, `witness`, `lookup`; canonical serialization; seeded probe generator
  `probegen/v1`; sandbox with 2× determinism check; Merkle `bsm1:` addresses; local registry;
  negative-control suite (12 refactor pairs, 11 mutant pairs).
