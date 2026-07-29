class MaximumDrawdownCalculator:
    """
    Calculates the maximum drawdown of a portfolio value series.
    """

    def calculate(self, portfolio_values):
        if not portfolio_values:
            raise ValueError("Portfolio values cannot be empty")

        peak = portfolio_values[0]
        max_drawdown = 0.0
        peak_index = 0
        trough_index = 0
        current_peak_index = 0

        for i, value in enumerate(portfolio_values):
            if value > peak:
                peak = value
                current_peak_index = i

            drawdown = (peak - value) / peak

            if drawdown > max_drawdown:
                max_drawdown = drawdown
                peak_index = current_peak_index
                trough_index = i

        return {
            "max_drawdown": max_drawdown,
            "max_drawdown_percent": round(max_drawdown * 100, 2),
            "peak_index": peak_index,
            "trough_index": trough_index,
        }
