from bisim.mutbench import mutants, oracle_probes, run_corpus, summarize
from bisim.extract import parse_signature
from bisim.probes import STANDARD_COUNT, generate_type_probes


def test_mutants_are_generated_and_compile():
    src = "def f(x: int) -> int:\n    if x > 0:\n        return x + 1\n    return 0\n"
    ms = mutants(src, "f")
    descs = [d for d, _ in ms]
    assert any(d.startswith("compare") for d in descs) and any(d.startswith("arith") for d in descs)
    assert any(d.startswith("negate-if") for d in descs) and any(d.startswith("const") for d in descs)
    for _, s in ms:
        compile(s, "<m>", "exec")


def test_oracle_probes_are_independent_of_standard():
    spec = parse_signature("def f(x: int, y: int = 1) -> int")
    std = {p.id for p in generate_type_probes(spec, STANDARD_COUNT)}
    orc = oracle_probes(spec, 200)
    assert len(orc) == 200 and not (std & {p.id for p in orc})
    assert {len(p.args) for p in orc} == {1, 2}


def test_run_corpus_small(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("def f(x: int) -> int:\n    if x > 100:\n        return 1\n    return 0\n")
    res = run_corpus([str(f)], oracle_n=100, log=lambda *a: None)
    assert res and all(r.oracle_differs is not None for r in res)
    changed = [r for r in res if r.oracle_differs]
    assert changed and all(r.standard_differs or r.grown_differs is not None for r in changed)
    text = summarize(res)
    assert "caught by the 48 standard probes" in text
