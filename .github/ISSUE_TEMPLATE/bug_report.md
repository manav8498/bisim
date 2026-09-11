---
name: Bug report
about: A wrong verdict, a target that should work but is refused, or a crash
labels: bug
---

**Command you ran**

**What happened** (paste the output, `--json` output is best)

**What you expected**

**A small file that reproduces it**

```python
```

**Versions**: output of `bisim --version` and `python --version`, and your OS.

If bisim gave a wrong verdict (SAME when the behavior differs, or CHANGED for a rewrite that keeps behavior), please include both versions of the code. Each of these becomes a test fixture.
