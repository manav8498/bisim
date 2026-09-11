import textwrap

from bisim.core import diff_targets, hash_target
from bisim.extract import extract_class

SRC = '''
import functools

class Circle:
    def __init__(self, r: int):
        self.r = r

    @property
    def area(self) -> int:
        return 3 * self.r * self.r

    @functools.cached_property
    def diameter(self) -> int:
        return 2 * self.r

    @staticmethod
    def unit() -> int:
        return 1

    @classmethod
    def from_diameter(cls, d: int) -> int:
        return d // 2

    @functools.lru_cache(maxsize=None)
    def scaled(self, k: int) -> int:
        return self.r * k

    def grow(self, k: int) -> None:
        self.r += k
'''


def test_extract_decorated_kinds(tmp_path):
    p = tmp_path / "c.py"
    p.write_text(textwrap.dedent(SRC))
    c = extract_class(str(p), "Circle")
    assert c.kinds == {"area": "property", "diameter": "property", "unit": "static", "from_diameter": "classmethod", "scaled": "method", "grow": "method"}
    assert c.methods["area"].params == [] and c.methods["from_diameter"].params == [("d", "int")]
    assert c.methods["unit"].params == [] and c.methods["scaled"].params == [("k", "int")]
    assert c.public_methods() == ["area", "diameter", "from_diameter", "grow", "scaled", "unit"]


def test_properties_are_read_not_called(tmp_path):
    p = tmp_path / "c.py"
    p.write_text(textwrap.dedent(SRC))
    m = hash_target(str(p), "Circle.area", root=tmp_path).manifest
    rec = m.probes[0].obs
    assert rec["ok"] and rec["steps"][0]["ok"] and rec["steps"][0]["value"][0] == "i"
    m = hash_target(str(p), "Circle", root=tmp_path).manifest
    assert m.coverage["pct"] == 100.0
    # a property that becomes a different formula flips the class address
    p2 = tmp_path / "c2.py"
    p2.write_text(textwrap.dedent(SRC).replace("return 3 * self.r * self.r", "return 3 * self.r * self.r + 1"))
    d = diff_targets(str(p), "Circle", str(p2), "Circle", root=tmp_path)
    assert not d.same
    # static/class methods are probed too
    d = diff_targets(str(p), "Circle.unit", str(p2), "Circle.unit", root=tmp_path)
    assert d.same
