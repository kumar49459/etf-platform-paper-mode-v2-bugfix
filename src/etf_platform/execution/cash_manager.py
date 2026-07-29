class CashManager:
    """
    Manages available cash for paper trading.
    """

    def __init__(self, initial_cash):
        self._cash = float(initial_cash)

    @property
    def balance(self):
        return self._cash

    def deposit(self, amount):
        self._cash += float(amount)

    def withdraw(self, amount):
        amount = float(amount)

        if amount > self._cash:
            raise ValueError("Insufficient cash")

        self._cash -= amount
