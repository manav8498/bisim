def f(xs: list[str]) -> str:
    if not xs:
        raise ValueError('empty')
    return max(xs, key=len)
