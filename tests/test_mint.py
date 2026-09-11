import subprocess
import sys

import pytest

from bisim.extract import parse_signature
from bisim.mint import Answer, Generation, Question, ScriptedAnswerer, emit_tests, mint, select_probe
from bisim.probes import Probe
from bisim.sandbox import SandboxError
from bisim.store import lookup
from bisim.witness import load_ledger

MEAN = (
    "def median(xs: list[float]) -> float:\n"
    "    if not xs:\n        raise ValueError('empty')\n"
    "    s = sorted(xs); n = len(s)\n"
    "    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n"
)
LOWER = (
    "def median(xs: list[float]) -> float:\n"
    "    if not xs:\n        raise ValueError('empty')\n"
    "    s = sorted(xs)\n    return s[(len(s) - 1) // 2]\n"
)
ZERO = (
    "def median(xs: list[float]) -> float:\n"
    "    if not xs:\n        return 0.0\n"
    "    s = sorted(xs); n = len(s)\n"
    "    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n"
)
MEAN2 = MEAN.replace("s = sorted(xs); n = len(s)", "n = len(xs); s = sorted(xs)")


class Fake:
    def __init__(self, cands=(MEAN, LOWER, ZERO)):
        self.calls = 0
        self.cands = list(cands)

    def generate(self, intent, spec, k, witnesses):
        self.calls += 1
        return Generation(self.cands, ["even length ambiguous", "empty ambiguous"], [[[1.0, 2.0, 3.0, 4.0]], [[]]])


class Recording(ScriptedAnswerer):
    def __init__(self, answers):
        super().__init__(answers)
        self.questions = []

    def ask(self, q):
        self.questions.append(q)
        return super().ask(q)


def test_select_probe_prefers_max_split_then_entropy_then_simplicity():
    probes = [Probe((1,)), Probe((2,)), Probe((3,)), Probe(([1, 2, 3, 4],))]
    # obs_by_cand[c][p]
    obs = [
        [{"v": 1}, {"v": 1}, {"v": 1}, {"v": 1}],
        [{"v": 1}, {"v": 2}, {"v": 2}, {"v": 2}],
        [{"v": 1}, {"v": 2}, {"v": 3}, {"v": 3}],
    ]
    assert select_probe(probes, obs, [0, 1, 2]) == 2  # 3-way split; among ties, the simplest input
    assert select_probe(probes, obs, [0, 1]) == 1  # all 2-way; the shortest input, first on ties
    assert select_probe(probes, [[{"v": 1}] * 4] * 3, [0, 1, 2]) is None
    # a suggested input beats a generated one of equal split quality
    probes2 = [Probe((2,)), Probe(([1, 2, 3, 4],), "suggested")]
    obs2 = [[{"v": 1}, {"v": 1}], [{"v": 2}, {"v": 2}]]
    assert select_probe(probes2, obs2, [0, 1]) == 1


def test_mint_loop_writes_everything(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")
    ans = Recording([Answer("choose", 0), Answer("choose", 0), Answer("choose", 0)])
    fake = Fake()
    r = mint("median of a list", spec, fake, ans, tmp_path, str(tmp_path / "median.py"), tests_dir=str(tmp_path / "tests"))
    assert r.address.startswith("bsm1:")
    assert (tmp_path / "median.py").read_text().startswith("def median")
    assert 1 <= r.questions_asked <= 5 and len(r.ledger.witnesses) == r.questions_asked
    # the first question must be about the input that splits all three behaviors
    q0 = ans.questions[0]
    assert q0.remaining_classes == 3 and len(q0.options) >= 2
    assert all(o.count >= 1 for o in q0.options) and sum(o.count for o in q0.options) == 3
    # persisted: ledger on disk, registry entry, tests that pass
    L = load_ledger(tmp_path, str(tmp_path / "median.py"), "median")
    assert len(L.witnesses) == r.questions_asked and L.witnesses[0].source == "mint" and L.suggested
    assert lookup(tmp_path, r.address)["meta"]["intent"] == "median of a list"
    assert r.test_path
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", r.test_path], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert fake.calls == 1


def test_mint_no_ambiguity_asks_nothing(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")
    r = mint("median", spec, Fake([MEAN, MEAN2]), ScriptedAnswerer([]), tmp_path, str(tmp_path / "m.py"))
    assert r.questions_asked == 0 and r.test_path is None


def test_mint_custom_output_and_exclude(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")
    ans = Recording([Answer("exclude"), Answer("output", value=2.5)])
    r = mint("median", spec, Fake(), ans, tmp_path, str(tmp_path / "m.py"))
    assert r.questions_asked == 2 and len(r.ledger.witnesses) == 1 and len(r.ledger.excluded) == 1
    assert ans.questions[0].probe.args == ([],)  # simplest suggested input first
    assert ans.questions[1].probe.args == ([1.0, 2.0, 3.0, 4.0],)
    assert (tmp_path / "m.py").read_text() in (MEAN, ZERO)


UPPER = (
    "def median(xs: list[float]) -> float:\n"
    "    if not xs:\n        raise ValueError('empty')\n"
    "    s = sorted(xs)\n    return s[len(s) // 2]\n"
)


def test_oracle_answerer_drives_to_the_reference(tmp_path):
    from bisim.mint import OracleAnswerer

    spec = parse_signature("def median(xs: list[float]) -> float")
    r = mint("median", spec, Fake([LOWER, ZERO, MEAN, UPPER]), OracleAnswerer(MEAN, "median"), tmp_path, str(tmp_path / "m.py"))
    assert (tmp_path / "m.py").read_text() == MEAN and r.questions_asked >= 2


def test_mint_quit_keeps_first_surviving(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")
    r = mint("median", spec, Fake(), ScriptedAnswerer([Answer("quit")]), tmp_path, str(tmp_path / "m.py"))
    assert r.questions_asked == 0 and r.ledger.witnesses == []


ZERO_LOWER = LOWER.replace("raise ValueError('empty')", "return 0.0")


def test_confirmation_recovers_a_pruned_disagreement(tmp_path):
    """LOWER and ZERO differ on [] and on even-length lists. The split loop asks about [] first,
    prunes ZERO, and is left with LOWER — whose even-length behavior the user never approved.
    A confirmation question on a once-contested even-length input catches it; regeneration
    with the witnesses then yields MEAN."""
    from bisim.mint import OracleAnswerer

    spec = parse_signature("def median(xs: list[float]) -> float")

    class Regen(Fake):
        def generate(self, intent, spec, k, witnesses):
            self.calls += 1
            if self.calls == 1:
                return Generation([LOWER, ZERO], [], [[[1.0, 2.0, 3.0, 4.0]], [[]]])
            return Generation([MEAN], [], [])

    c = Regen()
    r = mint("median", spec, c, OracleAnswerer(MEAN, "median"), tmp_path, str(tmp_path / "m.py"))
    assert (tmp_path / "m.py").read_text() == MEAN and c.calls == 2
    assert 2 <= r.questions_asked <= 4 and any(w.note.startswith("confirmed") for w in r.ledger.witnesses)
    # without confirmations the old protocol silently keeps LOWER
    c2 = Regen()
    r2 = mint("median", spec, c2, OracleAnswerer(MEAN, "median"), tmp_path, str(tmp_path / "m2.py"), max_confirm=0)
    assert (tmp_path / "m2.py").read_text() == LOWER and r2.questions_asked == 1 and c2.calls == 1


def test_confirmation_questions_are_flagged_and_quit_declines_them(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")
    ans = Recording([Answer("choose", 0), Answer("quit"), Answer("quit"), Answer("quit")])
    r = mint("median", spec, Fake([LOWER, ZERO]), ans, tmp_path, str(tmp_path / "m.py"))
    assert ans.questions[0].confirm is False and ans.questions[0].probe.args == ([],)
    assert ans.questions[1].confirm is True and len(ans.questions) <= 4
    assert r.questions_asked == 1 and len(r.ledger.witnesses) == 1


def test_mint_regenerates_when_no_candidate_matches(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")

    class Regen(Fake):
        def generate(self, intent, spec, k, witnesses):
            self.calls += 1
            if self.calls == 1:
                return Generation([LOWER, UPPER], [], [[[1.0, 2.0, 3.0, 4.0]]])
            assert witnesses, "second round must see the witnesses"
            return Generation([MEAN], [], [])

    ans = Recording([Answer("output", value=2.5)])
    client = Regen()
    r = mint("median", spec, client, ans, tmp_path, str(tmp_path / "m.py"))
    assert ans.questions[0].probe.args == ([1.0, 2.0, 3.0, 4.0],)
    assert (tmp_path / "m.py").read_text() == MEAN and r.questions_asked == 1 and client.calls == 2


def test_mint_no_candidate_matches_raises_but_keeps_witnesses(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")
    with pytest.raises(SandboxError, match="witnesses were saved"):
        mint("median", spec, Fake([LOWER, ZERO]), ScriptedAnswerer([Answer("output", value=99.0)]), tmp_path, str(tmp_path / "m.py"))
    assert len(load_ledger(tmp_path, str(tmp_path / "m.py"), "median").witnesses) == 1


def test_mint_ignores_broken_and_nondeterministic_candidates(tmp_path):
    spec = parse_signature("def median(xs: list[float]) -> float")
    broken = "def median(xs: list[float]) -> float:\n    return undefined_name\n"
    rnd = "import random\ndef median(xs: list[float]) -> float:\n    return random.random()\n"
    r = mint("median", spec, Fake([broken, rnd, MEAN]), ScriptedAnswerer([]), tmp_path, str(tmp_path / "m.py"))
    assert (tmp_path / "m.py").read_text() == MEAN


def test_emit_tests_covers_values_exceptions_and_nan():
    from bisim.witness import Ledger, add_witness

    spec = parse_signature("def f(x: float) -> float")
    L = Ledger("f")
    add_witness(L, [1.0], {"ok": True, "value": ["f", "2.0"]}, "double")
    add_witness(L, [-1.0], {"ok": False, "exc": "ValueError"})
    add_witness(L, [float("nan")], {"ok": True, "value": ["f", "nan"]})
    add_witness(L, [0.0], {"timeout": True})
    src = emit_tests(L, spec, "from m import f")
    assert "assert f(1.0) == 2.0" in src and "# double" in src
    assert "pytest.raises(ValueError)" in src and "math.isnan(f(nan))" in src or "math.isnan(f(float('nan')))" in src
    assert src.count("def test_witness_") == 3
