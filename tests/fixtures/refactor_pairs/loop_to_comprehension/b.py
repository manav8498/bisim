def f(xs: list[int]) -> int:
    return sum(x * x for x in xs if x % 2 == 0)
