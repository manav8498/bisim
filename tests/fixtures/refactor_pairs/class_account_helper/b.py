class C:
    def __init__(self, balance: int):
        self.balance = balance

    def _check(self, n: int) -> None:
        if n < 0:
            raise ValueError('negative')

    def deposit(self, n: int) -> int:
        self._check(n)
        self.balance = self.balance + n
        return self.balance
