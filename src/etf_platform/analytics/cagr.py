from datetime import datetime


class CAGRCalculator:
    """
    Compound Annual Growth Rate calculator.
    """

    def calculate(
        self,
        initial_value,
        final_value,
        start_date,
        end_date,
    ):
        if initial_value <= 0:
            raise ValueError("Initial value must be greater than zero")

        if final_value <= 0:
            raise ValueError("Final value must be greater than zero")

        if isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date)

        if isinstance(end_date, str):
            end_date = datetime.fromisoformat(end_date)

        years = (end_date - start_date).days / 365.25

        if years <= 0:
            raise ValueError("Invalid investment period")

        cagr = (final_value / initial_value) ** (1 / years) - 1

        return {
            "years": round(years, 4),
            "cagr": cagr,
            "cagr_percent": round(cagr * 100, 2),
        }
