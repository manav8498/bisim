def f(x: int, lo: int, hi: int) -> int:
    if x < lo:
        return lo
    else:
        if x > hi:
            return hi
        else:
            return x
