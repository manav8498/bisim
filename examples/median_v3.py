def median(xs: list[float]) -> float:
    """A plausible-but-different reading: the *lower* middle for even-length input."""
    if not xs:
        raise ValueError("median of empty list")
    s = sorted(xs)
    return float(s[(len(s) - 1) // 2])
