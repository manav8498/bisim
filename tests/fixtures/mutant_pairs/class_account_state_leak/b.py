class C:
    def __init__(self, balance: int):
        self.balance = balance

    def deposit(self, n: int) -> int:
        self.balance += n
        if n < 0:
            raise ValueError('negative')
        return self.balance
