class C:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.items: list[int] = []

    def push(self, x: int) -> None:
        if len(self.items) >= self.capacity:
            raise OverflowError('full')
        self.items.append(x)

    def pop(self) -> int:
        if not self.items:
            raise IndexError('empty')
        return self.items.pop()

    def size(self) -> int:
        return len(self.items)
