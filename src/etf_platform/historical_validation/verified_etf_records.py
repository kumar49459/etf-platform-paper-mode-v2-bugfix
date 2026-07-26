"""ETF documentation (Milestone 5A follow-up, requirement 2): first
trading date, benchmark mapping, for every symbol - VERIFIED against
real web sources where this environment's web_search/web_fetch tools
allow, replacing the design document's earlier "approximate, unverified"
placeholders.

IMPORTANT CAPABILITY CORRECTION, recorded here because it matters for how
much confidence to place in this file: earlier in this project I stated
this environment has "no live data access." That was wrong, stated too
broadly. web_search and web_fetch reach real, current external sources
(confirmed by retrieving real inception dates, benchmark names, and even
a live NIFTYBEES quote). What remains genuinely blocked is BULK HISTORICAL
DAILY OHLCV DATA spanning decades - the data behind every historical
price table is gated behind paid APIs (twelvedata.com, eodhd.com) this
environment cannot authenticate to, or requires NSE bhavcopy downloads
that need outbound network access bash_tool does not have. Fact-level
verification (this file) is possible; full historical backtesting is
still blocked pending either real data being supplied directly or a
credentialed API connection being made available.

Every entry below cites the sources checked and discloses cross-source
conflicts explicitly rather than silently picking one - exactly the
"benchmark mapping errors" and "missing/conflicting data" this milestone
asked to be actively searched for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class VerifiedETFRecord:
    symbol: str
    fund_name: str
    inception_date: date
    inception_date_confidence: str
    benchmark_index: str
    fund_house: str
    sources_checked: tuple
    notes: str = ""


VERIFIED_ETF_RECORDS = (
    VerifiedETFRecord(
        symbol="NIFTYBEES", fund_name="Nippon India ETF Nifty BeES", inception_date=date(2001, 12, 28),
        inception_date_confidence="verified", benchmark_index="Nifty 50 TRI", fund_house="Nippon India Mutual Fund",
        sources_checked=(
            "etf.nipponindiaim.com (official fund page)", "tradingview.com", "upstox.com",
            "scripbox.com", "nseindia.com",
        ),
        notes="India's first ETF. Consistent across every source checked (5+), including the fund's own "
              "official page. High confidence. Note: launched as Benchmark Mutual Fund's product; the "
              "managing AMC changed over time (Benchmark -> Goldman Sachs -> Nippon India), but the fund "
              "and its track record are continuous through that change - unverified against the AMC's own "
              "disclosure of exactly when each handover happened, flagged for follow-up if AMC-transition "
              "dates matter to a specific report.",
    ),
    VerifiedETFRecord(
        symbol="JUNIORBEES", fund_name="Nippon India ETF Nifty Next 50 Junior BeES", inception_date=date(2003, 2, 21),
        inception_date_confidence="conflicting_sources", benchmark_index="Nifty Next 50 TRI",
        fund_house="Nippon India Mutual Fund",
        sources_checked=("etf.nipponindiaim.com (official)", "tradingview.com", "scripbox.com", "upstox.com", "stockanalysis.com"),
        notes="CONFLICT FOUND AND NOT SILENTLY RESOLVED: the fund's own official page and 3 independent "
              "sources agree on 2003-02-21; stockanalysis.com states 2002-02-21 (exactly one year earlier). "
              "Using the official-source-corroborated 2003 date as primary, but this is a real, disclosed "
              "discrepancy, not a settled fact.",
    ),
    VerifiedETFRecord(
        symbol="BANKBEES", fund_name="Nippon India ETF Nifty Bank BeES", inception_date=date(2004, 5, 27),
        inception_date_confidence="conflicting_sources", benchmark_index="Nifty Bank TRI",
        fund_house="Nippon India Mutual Fund",
        sources_checked=("etf.nipponindiaim.com (official)", "stockanalysis.com", "upstox.com"),
        notes="CONFLICT FOUND AND RESOLVED VIA OFFICIAL-SOURCE CORROBORATION: the fund's own official page "
              "and an independent data provider both state 2004-05-27. One source (upstox.com) stated "
              "1996-04-01, which is almost certainly an error confusing the ETF's launch with the underlying "
              "Nifty Bank index's own base/launch date, or a different scheme entirely - 1996 predates "
              "India's first ETF (NIFTYBEES, 2001) by 5 years, which is not plausible for a Nifty-Bank-"
              "tracking ETF specifically. Flagged explicitly rather than silently using the wrong source.",
    ),
    VerifiedETFRecord(
        symbol="GOLDBEES", fund_name="Nippon India ETF Gold BeES", inception_date=date(2007, 3, 8),
        inception_date_confidence="verified", benchmark_index="Domestic price of physical gold (no equity index)",
        fund_house="Nippon India Mutual Fund",
        sources_checked=(
            "etf.nipponindiaim.com (official)", "valueresearchonline.com", "cbonds.com",
            "upstox.com", "indmoney.com", "businesstoday.in",
        ),
        notes="Consistent across 6+ independent sources including the fund's own official page and its "
              "ISIN registration date (cbonds.com). High confidence. No equity/TRI benchmark exists for "
              "this fund - tracking_difference.py measurements against a domestic gold price series, not "
              "an index, for any pre-inception proxy period.",
    ),
    VerifiedETFRecord(
        symbol="LIQUIDBEES", fund_name="Nippon India ETF Nifty 1D Rate Liquid BeES", inception_date=date(2003, 7, 8),
        inception_date_confidence="conflicting_sources", benchmark_index="Nifty 1D Rate TRI",
        fund_house="Nippon India Mutual Fund",
        sources_checked=(
            "mstock.com (2003-07-03)", "tradingview.com x2 (2003-06-08)", "valueresearchonline.com (2003-07-08)",
            "stockanalysis.com (2003-07-08)", "indmoney.com (July 2003, no exact day)",
        ),
        notes="UNRESOLVED CONFLICT, disclosed rather than guessed: sources split between June 8 and "
              "July 3/7/8, 2003 - a genuine multi-way disagreement, not a single outlier against a clear "
              "majority the way BANKBEES/JUNIORBEES were. Using July 8 (matching 2 of 5 sources including "
              "valueresearchonline.com) as a working value, but explicitly NOT claiming this is settled. "
              "Also worth noting: this fund's benchmark and underlying instrument type changed over its "
              "history (formerly 'Liquid BeES' tracking CBLO/repo instruments, renamed to 'Nifty 1D Rate "
              "Liquid BeES' with a different index methodology) - a real complication for any pre-rename "
              "historical comparison this package's tracking_difference.py would need to account for, not "
              "currently handled.",
    ),
)
