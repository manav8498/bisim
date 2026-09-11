def _abs(d: int) -> int:
    return d if d >= 0 else -d


def f(a: int, b: int) -> int:
    return _abs(a - b)
