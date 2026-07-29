from etf_platform.reporting.report_engine import ReportEngine


def test_generate_report():
    engine = ReportEngine()

    result = {
        "investment": 100000,
        "portfolio_value": 125000,
        "profit": 25000,
        "return_percent": 25.0,
    }

    report = engine.generate(
        strategy="SIP",
        result=result
    )

    assert isinstance(report, dict)
    assert "generated_at" in report
    assert report["strategy"] == "SIP"
    assert "summary" in report
    assert "details" in report


def test_summary_values():
    engine = ReportEngine()

    result = {
        "investment": 50000,
        "portfolio_value": 60000,
        "profit": 10000,
        "return_percent": 20.0,
    }

    report = engine.generate(
        strategy="SIP",
        result=result
    )

    assert report["summary"]["investment"] == 50000
    assert report["summary"]["portfolio_value"] == 60000
    assert report["summary"]["profit"] == 10000
