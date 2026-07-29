from datetime import datetime


class XIRRCalculator:
    """
    Calculates XIRR using the Newton-Raphson method.
    """

    def calculate(self, cashflows, guess=0.10):
        if len(cashflows) < 2:
            raise ValueError("At least two cashflows are required")

        dates = []
        amounts = []

        for date, amount in cashflows:
            if isinstance(date, str):
                date = datetime.fromisoformat(date)

            dates.append(date)
            amounts.append(float(amount))

        start = dates[0]

        def xnpv(rate):
            total = 0.0
            for dt, amt in zip(dates, amounts):
                years = (dt - start).days / 365.25
                total += amt / ((1 + rate) ** years)
            return total

        def dxnpv(rate):
            total = 0.0
            for dt, amt in zip(dates, amounts):
                years = (dt - start).days / 365.25
                total -= (
                    years * amt /
                    ((1 + rate) ** (years + 1))
                )
            return total

        rate = guess

        for _ in range(100):
            value = xnpv(rate)
            derivative = dxnpv(rate)

            if abs(derivative) < 1e-12:
                raise ValueError("Derivative too small")

            new_rate = rate - value / derivative

            if abs(new_rate - rate) < 1e-8:
                return {
                    "xirr": new_rate,
                    "xirr_percent": round(new_rate * 100, 2),
                }

            rate = new_rate

        raise ValueError("XIRR failed to converge")
