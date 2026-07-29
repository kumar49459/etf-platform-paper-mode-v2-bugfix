from etf_platform.analytics.cagr import CAGRCalculator
from etf_platform.analytics.xirr import XIRRCalculator
from etf_platform.analytics.max_drawdown import MaximumDrawdownCalculator
from etf_platform.analytics.volatility import VolatilityCalculator
from etf_platform.analytics.sharpe_ratio import SharpeRatioCalculator
from etf_platform.analytics.sortino_ratio import SortinoRatioCalculator
from etf_platform.analytics.calmar_ratio import CalmarRatioCalculator


def test_cagr():
    result = CAGRCalculator().calculate(
        100000,
        180000,
        "2020-01-01",
        "2025-01-01",
    )

    assert result["cagr_percent"] > 0


def test_xirr():
    cashflows = [
        ("2020-01-01", -100000),
        ("2021-01-01", -50000),
        ("2025-01-01", 220000),
    ]

    result = XIRRCalculator().calculate(cashflows)

    assert result["xirr_percent"] > 0


def test_drawdown():
    values = [
        100000,
        120000,
        90000,
        140000,
    ]

    result = MaximumDrawdownCalculator().calculate(values)

    assert result["max_drawdown_percent"] >= 0


def test_volatility():
    returns = [
        0.01,
        -0.01,
        0.02,
        0.00,
    ]

    result = VolatilityCalculator().calculate(returns)

    assert result["annualized_volatility"] >= 0


def test_sharpe():
    returns = [
        0.01,
        -0.01,
        0.02,
        0.00,
    ]

    result = SharpeRatioCalculator().calculate(returns)

    assert "sharpe_ratio" in result


def test_sortino():
    returns = [
        0.01,
        -0.01,
        0.02,
        0.00,
    ]

    result = SortinoRatioCalculator().calculate(returns)

    assert "sortino_ratio" in result


def test_calmar():
    result = CalmarRatioCalculator().calculate(
        cagr=0.18,
        max_drawdown=0.12,
    )

    assert result["calmar_ratio"] > 0
