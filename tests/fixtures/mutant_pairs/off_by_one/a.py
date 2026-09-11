def f(n: int) -> int:
    if n < 0 or n > 100000:
        return -1
    return sum(range(n))
