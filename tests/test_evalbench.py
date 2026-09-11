import json

import pytest

from bisim.evalbench import CachedClient, run_task, summarize

MEAN = "def median(xs: list[float]) -> float:\n    if not xs:\n        raise ValueError('empty')\n    s = sorted(xs); n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n"
LOWER = "def median(xs: list[float]) -> float:\n    if not xs:\n        raise ValueError('empty')\n    s = sorted(xs)\n    return s[(len(s) - 1) // 2]\n"
ZERO = "def median(xs: list[float]) -> float:\n    if not xs:\n        return 0.0\n    s = sorted(xs); n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n"
TASK = {"name": "median", "intent": "median", "sig": "def median(xs: list[float]) -> float", "reference": MEAN}


def test_cached_client_serves_from_disk_and_records(tmp_path):
    class Inner:
        def __init__(self):
            self.calls = 0

        def generate(self, *a):
            self.calls += 1
            from bisim.mint import Generation

            return Generation([LOWER, MEAN], ["n"], [[[1.0, 2.0, 3.0, 4.0]]])

    inner = Inner()
    c = CachedClient("median", str(tmp_path), inner)
    from bisim.extract import parse_signature

    spec = parse_signature(TASK["sig"])
    g1 = c.generate("median", spec, 2, [])
    g2 = CachedClient("median", str(tmp_path), None).generate("median", spec, 2, [])
    assert inner.calls == 1 and g1.candidates == g2.candidates == [LOWER, MEAN]
    assert json.load(open(tmp_path / "median.json"))["0"]["notes"] == ["n"]
    with pytest.raises(RuntimeError, match="no cached generation"):
        CachedClient("median", str(tmp_path), None).generate("median", spec, 2, [object()])


def test_run_task_from_cache_reports_pick1_vs_mint(tmp_path):
    json.dump({"0": {"candidates": [LOWER, ZERO, MEAN], "notes": [], "suggested_args": [[[1.0, 2.0, 3.0, 4.0]], [[]]]}},
              open(tmp_path / "median.json", "w"))
    r = run_task(TASK, str(tmp_path))
    assert r.candidates == 3 and r.classes == 3 and r.questions >= 1
    assert r.pick1_correct is False and r.mint_correct is True and not r.error
    table = summarize([r])
    assert "| median | 3 | 3 |" in table and "mint correct 1/1" in table
