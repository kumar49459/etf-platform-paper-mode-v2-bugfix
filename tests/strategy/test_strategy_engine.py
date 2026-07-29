import pytest

from etf_platform.strategy.strategy_engine import StrategyEngine


def test_unknown_strategy():
    engine = StrategyEngine()

    with pytest.raises(ValueError):
        engine.run(strategy="UNKNOWN")


def test_buy_and_hold():
    engine = StrategyEngine()

    result = engine.run(
        strategy="BUY_AND_HOLD",
        symbol="NIFTYBEES"
    )

    assert isinstance(result, dict)
    assert result["strategy"] == "BUY_AND_HOLD"
    assert result["symbol"] == "NIFTYBEES"
    assert "buy_price" in result
    assert "sell_price" in result


def test_sip():
    engine = StrategyEngine()

    result = engine.run(
        strategy="SIP",
        symbol="NIFTYBEES",
        investment_per_period=20000
    )

    assert isinstance(result, dict)
    assert "investment" in result
    assert "portfolio_value" in result


def test_portfolio_sip():
    engine = StrategyEngine()

    result = engine.run(
        strategy="PORTFOLIO_SIP",
        allocations={
            "NIFTYBEES": 100
        },
        investment_per_period=20000
    )

    assert isinstance(result, dict)
    assert "investment" in result
