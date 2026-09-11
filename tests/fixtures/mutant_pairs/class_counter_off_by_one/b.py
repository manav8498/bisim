class C:
    def __init__(self, start: int):
        self.n = start

    def inc(self) -> int:
        self.n += 1
        return self.n - 1
