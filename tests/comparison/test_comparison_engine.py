from etf_platform.comparison.comparison_engine import ComparisonEngine


def test_compare_multiple_strategies():
    engine = ComparisonEngine()

    results = engine.compare([
        {
            "strategy": "BUY_AND_HOLD",
            "symbol": "NIFTYBEES"
        },
        {
            "strategy": "SIP",
            "symbol": "NIFTYBEES",
            "investment_per_period": 20000
        }
    ])

    assert isinstance(results, list)
    assert len(results) == 2

    assert results[0]["strategy"] == "BUY_AND_HOLD"
    assert results[1]["strategy"] == "SIP"

    assert "result" in results[0]
    assert "result" in results[1]


def test_compare_single_strategy():
    engine = ComparisonEngine()

    results = engine.compare([
        {
            "strategy": "BUY_AND_HOLD",
            "symbol": "NIFTYBEES"
        }
    ])

    assert len(results) == 1
    assert results[0]["strategy"] == "BUY_AND_HOLD"
