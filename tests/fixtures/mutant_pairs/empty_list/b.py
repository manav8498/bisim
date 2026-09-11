def f(xs: list[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / len(xs)
