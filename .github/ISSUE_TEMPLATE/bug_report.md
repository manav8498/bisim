---
name: Bug report
about: Something is wrong — a refused target that should work, a wrong verdict, a crash
labels: bug
---

**What did you run?** (the exact `bisim …` command)

**What happened?** (paste the output; `--json` output is best)

**What did you expect?**

**Minimal target** (a small `.py` that reproduces it):

```python
```

**Environment:** `bisim --version`, `python --version`, OS.

If the bug is a *wrong verdict* (SAME when behavior differs, or CHANGED on a pure refactor), please
include both versions of the target — that is exactly the kind of case the negative-control suite
should grow with.
