class C:
    def __init__(self, start: int):
        self.n = start

    def inc(self) -> int:
        self.n = self.n + 1
        return self.n

    def get(self) -> int:
        return self.n
