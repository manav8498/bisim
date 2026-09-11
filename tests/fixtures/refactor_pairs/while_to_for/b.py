def f(s: str) -> int:
    n = 0
    for ch in s:
        if ch in 'aeiouAEIOU':
            n += 1
    return n
