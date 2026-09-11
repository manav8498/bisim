def f(xs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return sorted(xs, key=lambda t: t[0], reverse=True)
