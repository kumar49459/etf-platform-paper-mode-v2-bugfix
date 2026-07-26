"""Synthetic data generator -- used ONLY because this environment has no
live data access. This is a framework-validation tool, not a source of
historical truth.

STRUCTURAL SAFEGUARD, not just a naming convention: every bar this
generates has source="synthetic" set on the frozen OHLCVBar's own `source`
field (Phase 2, frozen -- this field already existed for exactly this
kind of provenance tracking). DataSource.SYNTHETIC (provenance.py) is
what report_builder.py actually checks to force the disclosure banner --
this generator's job is only to make sure that check has something
correct to find.
"""

from __future__ import annotations

import random
from datetime import timedelta

from etf_platform.data_engine.models import OHLCVBar


REGIME_PARAMETERS = {
    "Dot-com Crash": {"daily_drift": -0.0025, "daily_vol": 0.025},
    "Global Financial Crisis": {"daily_drift": -0.003, "daily_vol": 0.03},
    "2013 Taper Tantrum": {"daily_drift": -0.0015, "daily_vol": 0.02},
    "COVID Crash": {"daily_drift": -0.012, "daily_vol": 0.045},
    "2022 Bear Market": {"daily_drift": -0.0008, "daily_vol": 0.015},
    "Recent Recovery Period": {"daily_drift": 0.0006, "daily_vol": 0.011},
    "_default_calm": {"daily_drift": 0.0004, "daily_vol": 0.010},
}
"""Parameters chosen only to be DIRECTIONALLY plausible for a labeled
synthetic-data dry run (a crash regime drifts down, a recovery regime
drifts up) -- NOT calibrated against any real historical measurement."""


def generate_synthetic_bars(symbol, start_date, end_date, seed, regime_name=None, starting_price=100.0):
    rng = random.Random(seed)
    params = REGIME_PARAMETERS.get(regime_name, REGIME_PARAMETERS["_default_calm"])
    drift, vol = params["daily_drift"], params["daily_vol"]

    bars = []
    price = starting_price
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            daily_return = rng.gauss(drift, vol)
            price = max(price * (1 + daily_return), 0.01)
            open_price = price * (1 - abs(rng.gauss(0, vol / 4)))
            high = max(open_price, price) * (1 + abs(rng.gauss(0, vol / 4)))
            low = min(open_price, price) * (1 - abs(rng.gauss(0, vol / 4)))
            bars.append(OHLCVBar(
                symbol=symbol, trade_date=current, open=round(open_price, 2), high=round(high, 2),
                low=round(low, 2), close=round(price, 2), volume=rng.randint(10000, 500000),
                adjusted_close=round(price, 2), source="synthetic",
            ))
        current += timedelta(days=1)
    return bars
