# Changelog

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
