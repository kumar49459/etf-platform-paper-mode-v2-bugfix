from pathlib import Path
import yaml


class ETFUniverse:

    def __init__(self):
        config_dir = Path(__file__).parent

        with (config_dir / "etf_universe.yaml").open(
            "r",
            encoding="utf-8",
        ) as f:
            self.data = yaml.safe_load(f)

        with (config_dir / "base_prices.yaml").open(
            "r",
            encoding="utf-8",
        ) as f:
            self.base_prices = yaml.safe_load(f)

    def category(self, name):
        return self.data.get(name, [])

    def all_symbols(self):
        symbols = []

        for values in self.data.values():
            symbols.extend(values)

        return sorted(set(symbols))

    def price(self, symbol):
        return self.base_prices.get(symbol)

    def prices(self):
        return dict(self.base_prices)
