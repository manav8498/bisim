import textwrap

from bisim.extract import executable_lines, extract_function
from bisim.probes import Probe, generate_type_probes
from bisim.sandbox import run_probes_with_coverage

SRC = '''
def f(x: int) -> int:
    """doc"""
    if x > 100:
        return 1
    elif x < -100:
        return -1
    else:
        return 0
'''


def test_executable_lines_excludes_def_and_docstring(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(SRC))
    spec = extract_function(str(p), "f")
    assert executable_lines(spec) == [4, 5, 6, 7, 9]


def test_coverage_per_probe(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(SRC))
    obs, cov = run_probes_with_coverage(str(p), "f", [Probe((500,)), Probe((0,))])
    assert obs == [{"ok": True, "value": ["i", "1"]}, {"ok": True, "value": ["i", "0"]}]
    assert 5 in cov[0] and 9 not in cov[0]
    assert 9 in cov[1] and 5 not in cov[1]
    assert all("cov" not in o for o in obs)  # coverage never leaks into observations


def test_hash_reports_coverage_and_missed_lines(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(SRC))
    from bisim.core import hash_function

    r = hash_function(str(p), "f", root=tmp_path)
    c = r.manifest.coverage
    assert c["executable"] == [4, 5, 6, 7, 9] and c["pct"] == 100.0 and c["missed"] == []
    # a branch nothing reaches
    p.write_text(textwrap.dedent(SRC).replace("x > 100", "x == 123456789"))
    r = hash_function(str(p), "f", root=tmp_path)
    assert 5 in r.manifest.coverage["missed"] and r.manifest.coverage["pct"] < 100.0
    assert any("uncovered" in w for w in r.warnings)


def test_diff_grows_probes_until_plateau(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    # differs only on a narrow input band that 48 boundary/seeded probes are unlikely to hit
    a.write_text("def f(x: int) -> int:\n    if 7000 < x < 7100:\n        return 1\n    return 0\n")
    b.write_text("def f(x: int) -> int:\n    return 0\n")
    from bisim.core import diff_functions

    d = diff_functions(str(a), "f", str(b), "f", root=tmp_path, grow=True)
    assert d.probe_count > 48  # growth happened because line 3 stayed uncovered
    assert d.growth_rounds >= 1
