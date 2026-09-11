# Contributing

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q                      # everything is offline; ~35 s
python -m bisim.evalbench                # reproduces the evaluation from cached generations
bash examples/demo.sh; bash examples/merge_demo.sh
```

Rules that keep the primitive honest:

- **Never change what feeds the address silently.** The address is `sha256("bisim/1" | probegen | sig_hash | merkle_root)`.
  Anything that changes probe generation must bump `PROBEGEN_VERSION`; anything that changes observation
  records must be documented in the spec amendments and must keep the negative-control suite green.
- **Refuse loudly.** No address is ever minted for a target we cannot execute deterministically.
- **Every "changed" verdict carries an input.** Keep it that way.
- Add a refactor pair and a mutant pair under `tests/fixtures/` for any new kind of target.
- The evaluation harness is a measurement, not a target: add held-out tasks, do not tune on them.

Design spec: `docs/superpowers/specs/2026-09-11-bisim-design.md` (with amendments). Plan: `docs/superpowers/plans/`.
