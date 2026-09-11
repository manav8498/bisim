def f(xs: list[int]) -> int:
    t = 0
    for x in xs:
        if x % 2 == 0:
            t += x * x
    return t
