def f(xs: list[float]) -> float:
    if not xs:
        raise ValueError('empty')
    s = sorted(xs)
    return s[(len(s) - 1) // 2]
