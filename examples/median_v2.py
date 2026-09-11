def median(xs: list[float]) -> float:
    """Same behavior as median_v1, written differently (a refactor)."""
    if len(xs) == 0:
        raise ValueError("median of empty list")
    ordered = sorted(xs)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return float(ordered[middle])
    lower, upper = ordered[middle - 1], ordered[middle]
    return (lower + upper) / 2
