import subprocess
import sys

from bisim.extract import parse_class_stub
from bisim.mint import Answer, Generation, OracleAnswerer, ScriptedAnswerer, emit_tests, mint

STUB = "class Stack:\n    def __init__(self, capacity: int): ...\n    def push(self, x: int) -> None: ...\n    def pop(self) -> int: ...\n"

LIFO = """class Stack:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.items: list[int] = []

    def push(self, x: int) -> None:
        if len(self.items) >= self.capacity:
            raise OverflowError("full")
        self.items.append(x)

    def pop(self) -> int:
        if not self.items:
            raise IndexError("empty")
        return self.items.pop()
"""
FIFO = LIFO.replace("return self.items.pop()", "return self.items.pop(0)")
DROP = LIFO.replace('        if len(self.items) >= self.capacity:\n            raise OverflowError("full")\n', "        if len(self.items) >= self.capacity:\n            return None\n")


class Fake:
    def __init__(self, cands):
        self.cands, self.calls = list(cands), 0

    def generate(self, intent, spec, k, witnesses):
        self.calls += 1
        return Generation(self.cands, ["LIFO vs FIFO", "overflow raises vs drops"],
                          [{"init": [2], "calls": [["push", [1]], ["push", [2]], ["pop", []]]},
                           {"init": [1], "calls": [["push", [1]], ["push", [2]]]}])


def test_parse_class_stub():
    c = parse_class_stub(STUB)
    assert c.name == "Stack" and c.init_params == [("capacity", "int")] and sorted(c.methods) == ["pop", "push"]


def test_mint_class_with_oracle(tmp_path):
    spec = parse_class_stub(STUB)
    fake = Fake([FIFO, DROP, LIFO])
    r = mint("a bounded LIFO stack", spec, fake, OracleAnswerer(LIFO, "Stack"), tmp_path, str(tmp_path / "stack.py"), tests_dir=str(tmp_path / "tests"))
    assert (tmp_path / "stack.py").read_text() == LIFO and r.address.startswith("bsm1:")
    assert r.questions_asked >= 2 and r.ledger.witnesses
    assert any("push" in w.display and "pop" in w.display for w in r.ledger.witnesses)
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", r.test_path], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    src = open(r.test_path).read()
    assert "Stack(" in src and ".push(" in src and (".pop() ==" in src or "pytest.raises" in src)


def test_mint_class_scripted_choose(tmp_path):
    spec = parse_class_stub(STUB)
    r = mint("stack", spec, Fake([FIFO, LIFO]), ScriptedAnswerer([Answer("choose", 1)] + [Answer("choose", 0)] * 5), tmp_path, str(tmp_path / "s.py"))
    assert (tmp_path / "s.py").read_text() in (FIFO, LIFO) and r.questions_asked >= 1


def test_emit_tests_sequences():
    from bisim.witness import Ledger, add_witness

    spec = parse_class_stub(STUB)
    L = Ledger("Stack")
    add_witness(L, [(2,), (("push", (1,)), ("push", (2,)), ("pop", ()))], {"ok": True, "steps": [{"ok": True, "value": ["n"]}, {"ok": True, "value": ["n"]}, {"ok": True, "value": ["i", "2"]}], "state": ["d", []]}, "LIFO")
    add_witness(L, [(0,), (("push", (1,)),)], {"ok": True, "steps": [{"ok": False, "exc": "OverflowError"}], "state": ["d", []]})
    add_witness(L, [(-1,), ()], {"ok": False, "exc": "ValueError", "at": "init"})
    src = emit_tests(L, spec, "from s import Stack")
    assert "obj = Stack(2)" in src and "obj.push(1)" in src and "assert obj.pop() == 2" in src
    assert "with pytest.raises(OverflowError):\n        obj.push(1)" in src
    assert "with pytest.raises(ValueError):\n        Stack(-1)" in src
