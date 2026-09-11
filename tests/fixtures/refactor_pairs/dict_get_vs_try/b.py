def f(d: dict[str, int], k: str) -> int:
    try:
        return d[k]
    except KeyError:
        return 0
