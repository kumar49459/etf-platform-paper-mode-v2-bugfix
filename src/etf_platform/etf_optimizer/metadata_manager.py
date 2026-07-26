"""ETF Metadata Manager (Phase 3).

Merges two sources per symbol:
  1. HistoricalDataEngine.get_instrument_master() — symbol, name, exchange,
     instrument_token (from NSE/Kite, via the existing Phase 2 provider
     abstraction, reused as-is per Phase 3's requirement).
  2. The overrides file (config/etf_metadata_overrides.yaml) — asset_class,
     index_tracked, issuer, expense_ratio, AUM (fields NSE/Kite don't
     provide at all).

Merge policy: override fields always win when present (they're the only
source for those fields anyway); provider fields (name, exchange) are used
unless the override explicitly supplies a non-null replacement. A symbol
with no override entry still gets an ETFMetadata built from provider data
alone (metadata_source="provider_only") — it isn't dropped, it's just less
complete, and downstream consumers (screening, scoring) are required to
handle that explicitly rather than assume every field is populated.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine import HistoricalDataEngine
from etf_platform.etf_optimizer.exceptions import MetadataError
from etf_platform.etf_optimizer.models import ETFMetadata

logger = get_logger("etf_optimizer.metadata_manager")


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class ETFMetadataManager:
    """Merges Data Engine provider metadata with the overrides file to
    produce a complete-as-possible ETFMetadata per symbol."""

    def __init__(self, data_engine: HistoricalDataEngine, overrides_path: str | Path) -> None:
        self._data_engine = data_engine
        self._overrides_path = Path(overrides_path)
        self._overrides: dict[str, dict[str, Any]] = self._load_overrides()

    def _load_overrides(self) -> dict[str, dict[str, Any]]:
        if not self._overrides_path.exists():
            logger.warning(
                "No metadata overrides file found at %s — proceeding with provider-only metadata "
                "for all symbols (asset_class/index_tracked/expense_ratio/AUM will be unavailable).",
                self._overrides_path,
            )
            return {}
        try:
            content = yaml.safe_load(self._overrides_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise MetadataError(f"Failed to parse metadata overrides file {self._overrides_path}: {exc}") from exc
        etfs = content.get("etfs", {})
        if not isinstance(etfs, dict):
            raise MetadataError(f"Metadata overrides file {self._overrides_path}: 'etfs' must be a mapping.")
        return {symbol.upper(): fields for symbol, fields in etfs.items()}

    def get_metadata(self, symbol: str) -> ETFMetadata:
        symbol = symbol.upper()
        provider_instruments = {
            inst.symbol: inst for inst in self._data_engine.get_instrument_master()
        }
        provider_meta = provider_instruments.get(symbol)
        override = self._overrides.get(symbol, {})

        name = override.get("name") or (provider_meta.name if provider_meta else None) or symbol
        exchange = (provider_meta.exchange if provider_meta else None) or "NSE"

        has_override = symbol in self._overrides
        has_provider = provider_meta is not None
        if has_override and has_provider:
            source = "merged"
        elif has_override:
            source = "override_only"
        elif has_provider:
            source = "provider_only"
        else:
            source = "unknown"
            logger.warning(
                "No metadata found for '%s' from either the Data Engine instrument master or the "
                "overrides file. Returning a minimal placeholder — this symbol will likely fail "
                "screening for lack of data.",
                symbol,
            )

        return ETFMetadata(
            symbol=symbol,
            name=name,
            exchange=exchange,
            asset_class=override.get("asset_class"),
            index_tracked=override.get("index_tracked"),
            issuer=override.get("issuer"),
            inception_date=_parse_date(override.get("inception_date")),
            expense_ratio=override.get("expense_ratio"),
            tracking_error_pct=override.get("tracking_error_pct"),
            aum_crores=override.get("aum_crores"),
            aum_as_of=_parse_date(override.get("aum_as_of")),
            metadata_source=source,
        )

    def get_universe_metadata(self, symbols: list[str]) -> dict[str, ETFMetadata]:
        return {symbol.upper(): self.get_metadata(symbol) for symbol in symbols}

    def needs_verification(self, symbol: str) -> bool:
        return bool(self._overrides.get(symbol.upper(), {}).get("needs_verification", False))
