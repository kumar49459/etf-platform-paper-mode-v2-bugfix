"""Data provider adapters (NSE, Kite, and future paid vendors).

All providers implement the same `DataProvider` interface (base.py) so that
the HistoricalDataEngine and everything downstream never depends on a
specific vendor's data format — see Phase 1 §12.6 (binding provider
abstraction requirement).
"""
