def median(xs: list[float]) -> float:
    """Median: middle element, or the mean of the two middle elements. Empty input is an error."""
    if not xs:
        raise ValueError("median of empty list")
    s = sorted(xs)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2
