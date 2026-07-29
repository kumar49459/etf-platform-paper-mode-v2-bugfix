class DrawdownCurveGenerator:
    """
    Generates the drawdown curve from an equity curve.
    """

    def generate(self, equity_curve):
        if not equity_curve:
            raise ValueError("Equity curve cannot be empty")

        peak = equity_curve[0]
        curve = []

        for value in equity_curve:
            if value > peak:
                peak = value

            drawdown = (peak - value) / peak

            curve.append({
                "equity": value,
                "peak": peak,
                "drawdown": drawdown,
                "drawdown_percent": round(drawdown * 100, 2),
            })

        return curve
