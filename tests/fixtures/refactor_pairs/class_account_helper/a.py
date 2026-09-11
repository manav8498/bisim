class C:
    def __init__(self, balance: int):
        self.balance = balance

    def deposit(self, n: int) -> int:
        if n < 0:
            raise ValueError('negative')
        self.balance += n
        return self.balance
