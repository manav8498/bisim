class C:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.items: list[int] = []

    def push(self, x: int) -> None:
        if len(self.items) >= self.capacity:
            raise OverflowError('full')
        self.items.append(x)

    def pop(self) -> int:
        if len(self.items) == 0:
            raise IndexError('nothing')
        last = self.items[-1]
        del self.items[-1]
        return last

    def size(self) -> int:
        return len(self.items)
