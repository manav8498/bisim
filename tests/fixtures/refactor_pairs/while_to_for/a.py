def f(s: str) -> int:
    i = 0
    n = 0
    while i < len(s):
        if s[i] in 'aeiouAEIOU':
            n += 1
        i += 1
    return n
