# Contributing

## Setup

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q                  # all tests run offline, about 35 seconds
python -m bisim.evalbench            # reruns the mint evaluation from cached model output
bash examples/demo.sh
bash examples/merge_demo.sh
```

## Rules

- Do not change what goes into an address without reading `docs/design.md`. Changes to input generation need a new `PROBEGEN_VERSION`. Changes to result records need a note in the changelog and must keep the fixture tests green.
- Never produce an address for code that could not be run deterministically. Refuse and explain.
- Every "changed" result must include an input and both results.
- When you add support for a new kind of target, add one refactor pair and one mutant pair under `tests/fixtures/`.
- The evaluation sets in `examples/bench/` are measurements. Add new held-out tasks if you want more data. Do not tune the question strategy against the held-out set.

## Reporting a wrong verdict

The most useful bug report is a pair of files where bisim says SAME but the behavior differs, or says CHANGED for a rewrite that preserves behavior. Include both files and the command you ran. Each of these becomes a new fixture.
