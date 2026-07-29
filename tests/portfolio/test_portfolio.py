from etf_platform.portfolio.portfolio import Portfolio


def test_empty_portfolio():
    p = Portfolio()

    assert p.holdings() == {}
    assert p.total_units() == 0


def test_add_units():
    p = Portfolio()

    p.add("NIFTYBEES", 10)

    assert p.total_units() == 10


def test_add_multiple_times():
    p = Portfolio()

    p.add("NIFTYBEES", 10)
    p.add("NIFTYBEES", 5)

    assert p.total_units() == 15


def test_remove_units():
    p = Portfolio()

    p.add("NIFTYBEES", 20)
    p.remove("NIFTYBEES", 8)

    assert p.total_units() == 12


def test_remove_all_units():
    p = Portfolio()

    p.add("NIFTYBEES", 20)
    p.remove("NIFTYBEES", 20)

    assert p.total_units() == 0


def test_portfolio_value():
    p = Portfolio()

    p.add("NIFTYBEES", 10)
    p.add("MON100", 5)

    prices = {
        "NIFTYBEES": 250,
        "MON100": 150
    }

    assert p.value(prices) == 3250
