"""Market regime definitions (Milestone 5A, requirement 3). The six
mandatory regimes, exactly as specified, with dates carried over from the
design document's regime table - still unverified against a live source,
which matters more here than anywhere else in this package since these
dates are the ground truth for every regime-segmented report this module
produces.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MarketRegime:
    name: str
    start_date: date
    end_date: date
    description: str
    dates_verified: bool


MANDATORY_REGIMES = (
    MarketRegime(
        name="Dot-com Crash", start_date=date(2000, 2, 1), end_date=date(2001, 9, 30),
        description="Global technology-sector crash; Indian market impact compounded by the 2001 Ketan Parekh "
                     "scam. Every mandatory symbol in this platform's universe requires INDEX_PROXY data for "
                     "this entire regime -- no ETF existed yet.",
        dates_verified=False,
    ),
    MarketRegime(
        name="Global Financial Crisis", start_date=date(2008, 1, 1), end_date=date(2009, 3, 31),
        description="Approximately 60% peak-to-trough decline (unverified figure). GOLDBEES requires "
                     "INDEX_PROXY data throughout; other symbols may have thin-liquidity early ETF data.",
        dates_verified=False,
    ),
    MarketRegime(
        name="2013 Taper Tantrum", start_date=date(2013, 5, 1), end_date=date(2013, 9, 30),
        description="Fed tapering triggered an India-specific INR/capital-flight stress event, short but "
                     "sharp. Real ETF data available for all mandatory symbols.",
        dates_verified=False,
    ),
    MarketRegime(
        name="COVID Crash", start_date=date(2020, 2, 1), end_date=date(2020, 3, 31),
        description="Extremely fast decline, approximately 38% in ~5 weeks (unverified figure). Real ETF "
                     "data available for all mandatory symbols.",
        dates_verified=False,
    ),
    MarketRegime(
        name="2022 Bear Market", start_date=date(2022, 1, 1), end_date=date(2022, 6, 30),
        description="FII outflows, RBI rate hikes, Ukraine war. Overlaps the rising-rate/inflationary period "
                     "flagged as an open item in the design document -- treated here as one regime, not two, "
                     "pending your explicit confirmation.",
        dates_verified=False,
    ),
    MarketRegime(
        name="Recent Recovery Period", start_date=date(2023, 1, 1), end_date=date(2026, 7, 18),
        description="End date set to this platform's current operating date -- will need updating as time "
                     "passes rather than being treated as a fixed historical window.",
        dates_verified=False,
    ),
)


def regime_for_date(as_of, regimes=MANDATORY_REGIMES):
    for regime in regimes:
        if regime.start_date <= as_of <= regime.end_date:
            return regime
    return None
