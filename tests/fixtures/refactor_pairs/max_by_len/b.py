def f(xs: list[str]) -> str:
    if not xs:
        raise ValueError('empty')
    best = xs[0]
    for x in xs[1:]:
        if len(x) > len(best):
            best = x
    return best
