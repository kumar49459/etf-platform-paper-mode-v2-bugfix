from etf_platform.smart.smart_sip import SmartSIP


def test_smart_sip_runs():
    engine = SmartSIP()

    result = engine.run(
        symbol="NIFTYBEES",
        monthly_investment=20000
    )

    assert isinstance(result, dict)
    assert result["strategy"] == "SMART_SIP"
    assert "investment" in result
    assert "portfolio_value" in result
    assert "profit" in result
    assert "return_percent" in result


def test_custom_parameters():
    engine = SmartSIP()

    result = engine.run(
        symbol="NIFTYBEES",
        monthly_investment=10000,
        dip_threshold=3,
        dip_multiplier=3
    )

    assert result["strategy"] == "SMART_SIP"
    assert result["investment"] > 0
