"""Symbol Resolver.

This is a deliberately scoped subset of the full Symbol Resolution Engine
(Phase 1 Module 17, planned for Phase 9) — just enough for KiteProvider to
resolve a trading symbol to a Kite instrument_token, and to detect when that
mapping changes between refreshes (relisting, symbol rename), per the
mandatory Data Engine behavior in Phase 1 §12.6 ("symbol-change detection").
Phase 9 will extend this into the full standalone module with manual
override tables etc.; this class is written so that extension is additive,
not a rewrite — it already separates "fetch mapping," "detect changes," and
"resolve" into distinct methods a fuller implementation can build on.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.exceptions import SymbolResolutionError
from etf_platform.data_engine.models import InstrumentMeta, SymbolChangeEvent

logger = get_logger("data_engine.symbol_resolver")


class SymbolResolver:
    """Resolves ETF trading symbols to Kite instrument tokens and detects mapping changes between refreshes. Scoped subset of the full Symbol Resolution Engine (Phase 1 Module 17)."""
    def __init__(
        self,
        instrument_master_fetcher: Callable[[], list[InstrumentMeta]],
        cache_path: str | Path,
    ) -> None:
        self._fetch_master = instrument_master_fetcher
        self._cache_path = Path(cache_path)
        self._mapping: dict[str, int] = self._load_cache()

    def _load_cache(self) -> dict[str, int]:
        if not self._cache_path.exists():
            return {}
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load symbol resolver cache at %s: %s", self._cache_path, exc)
            return {}

    def _save_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._mapping, sort_keys=True, indent=2), encoding="utf-8")

    def refresh(self) -> list[SymbolChangeEvent]:
        """Fetch the current instrument master and diff it against the cached
        mapping. Returns SymbolChangeEvents for anything that changed or
        disappeared — callers (HistoricalDataEngine) are expected to log these
        as data-quality-relevant events, per Phase 1 §12.6 point 3."""
        new_master = self._fetch_master()
        new_mapping = {
            im.symbol: im.instrument_token
            for im in new_master
            if im.instrument_token is not None
        }

        events: list[SymbolChangeEvent] = []
        now = datetime.now(timezone.utc)

        for symbol, old_token in self._mapping.items():
            new_token = new_mapping.get(symbol)
            if new_token is None:
                events.append(
                    SymbolChangeEvent(
                        symbol=symbol,
                        old_instrument_token=old_token,
                        new_instrument_token=None,
                        detected_at=now,
                        detail="Symbol no longer present in instrument master (possible delisting).",
                    )
                )
            elif new_token != old_token:
                events.append(
                    SymbolChangeEvent(
                        symbol=symbol,
                        old_instrument_token=old_token,
                        new_instrument_token=new_token,
                        detected_at=now,
                        detail="Instrument token changed for existing symbol.",
                    )
                )

        added = set(new_mapping) - set(self._mapping)
        if added:
            logger.info("SymbolResolver: %d new symbol(s) added to instrument master.", len(added))

        if events:
            logger.warning("SymbolResolver detected %d symbol change event(s): %s", len(events), events)

        self._mapping = new_mapping
        self._save_cache()
        return events

    def resolve(self, symbol: str) -> int:
        token = self._mapping.get(symbol.upper())
        if token is None:
            raise SymbolResolutionError(
                f"No instrument_token cached for symbol '{symbol}'. Call refresh() first, "
                "or the symbol may not exist in the current instrument master."
            )
        return token

    def is_known(self, symbol: str) -> bool:
        return symbol.upper() in self._mapping
