from etf_platform.importer.import_summary import ImportSummary


def test_import_summary():
    summary = ImportSummary().summarize(
        "data/historical/NIFTYBEES.csv"
    )

    assert summary["valid"] is True
    assert summary["rows"] > 0
    assert summary["duplicate_count"] >= 0
    assert summary["gap_count"] >= 0

    assert summary["columns"] == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
