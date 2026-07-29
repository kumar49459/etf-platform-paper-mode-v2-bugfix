from etf_platform.session.backtest_session import BacktestSession


def test_buy_and_hold_session():
    session = BacktestSession()

    report = session.run(
        strategy="BUY_AND_HOLD",
        symbol="NIFTYBEES"
    )

    assert isinstance(report, dict)
    assert report["strategy"] == "BUY_AND_HOLD"
    assert "generated_at" in report
    assert "summary" in report
    assert "details" in report


def test_sip_session():
    session = BacktestSession()

    report = session.run(
        strategy="SIP",
        symbol="NIFTYBEES",
        investment_per_period=20000
    )

    assert isinstance(report, dict)
    assert report["strategy"] == "SIP"
    assert "summary" in report
