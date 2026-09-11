def f(s: str) -> str:
    words = s.split()
    out = []
    i = len(words) - 1
    while i >= 0:
        out.append(words[i])
        i -= 1
    return ' '.join(out)
