"""HistoricalDataEngine — the public facade for Phase 1 Module 1.

Public interface matches what was already committed to in
PHASE1_Architecture_SRS.md §4:
    get_ohlcv(symbols, start, end, snapshot_id=None)
    get_corporate_actions(symbol)
    get_instrument_master()
plus `ingest()`, which is new surface area needed to actually produce
snapshots (§4 described the read interface; Phase 2 is where the write path
gets designed).

Provider fallback: NSE (primary) is tried first for OHLCV; if it returns no
rows or raises DataProviderError, Kite (secondary) is tried. This directly
implements the primary/secondary decision from Phase 1 §12.6 — it is not
configurable per-call, only per-environment via config, so behavior stays
predictable and auditable.

Every symbol's data passes through DataQualityValidator before being
persisted — nothing bypasses this gate, matching Phase 1 §4's binding
requirement that "nothing downstream reads raw data directly."
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from etf_platform.common.logging_setup import get_logger
from etf_platform.config_manager.schema import AppConfig
from etf_platform.data_engine.exceptions import DataProviderError, SnapshotNotFoundError
from etf_platform.data_engine.models import CorporateAction, DataSnapshot, InstrumentMeta, OHLCVBar, SymbolChangeEvent
from etf_platform.data_engine.providers.base import DataProvider
from etf_platform.data_engine.providers.kite_provider import KiteProvider
from etf_platform.data_engine.providers.nse_provider import NSEProvider
from etf_platform.data_engine.rate_limiter import RateLimiter
from etf_platform.data_engine.snapshot_manager import generate_snapshot_id
from etf_platform.data_engine.storage import SnapshotRegistry, build_timeseries_store
from etf_platform.data_engine.symbol_resolver import SymbolResolver
from etf_platform.data_quality import DataQualityValidator
from etf_platform.data_quality.exceptions import CriticalDataQualityError
from etf_platform.secrets_manager import SecretsManager

logger = get_logger("data_engine")


class HistoricalDataEngine:
    """Public facade for the Historical Data Engine (Phase 1 Module 1). Fetches, validates, and persists OHLCV/corporate-action data across NSE/Kite with automatic fallback, snapshot versioning, and gap/quality gating."""
    def __init__(self, config: AppConfig, secrets_manager: SecretsManager | None = None) -> None:
        de_config = config.data_engine
        self._config = de_config

        self._store = build_timeseries_store(de_config.storage_backend, de_config.storage_path)
        self._registry = SnapshotRegistry(de_config.snapshot_registry_db)
        self._validator = DataQualityValidator(
            max_price_jump_pct=de_config.max_price_jump_pct,
            stale_price_max_days=de_config.stale_price_max_days,
        )

        self._symbol_resolver: SymbolResolver | None = None
        self._providers: dict[str, DataProvider] = self._build_providers(secrets_manager)

    def _build_providers(self, secrets_manager: SecretsManager | None) -> dict[str, DataProvider]:
        providers: dict[str, DataProvider] = {}
        rate_limits = self._config.rate_limits

        nse_rl = rate_limits.get("nse")
        if nse_rl is not None:
            providers["nse"] = NSEProvider(
                rate_limiter=RateLimiter(nse_rl.calls_per_second, nse_rl.calls_per_minute)
            )

        kite_rl = rate_limits.get("kite")
        if kite_rl is not None and secrets_manager is not None:
            kite_provider = KiteProvider(
                rate_limiter=RateLimiter(kite_rl.calls_per_second, kite_rl.calls_per_minute),
                secrets_manager=secrets_manager,
            )
            resolver = SymbolResolver(
                instrument_master_fetcher=kite_provider.fetch_instrument_master,
                cache_path=f"{self._config.storage_path}/_symbol_resolver_cache.json",
            )
            kite_provider.attach_symbol_resolver(resolver)
            self._symbol_resolver = resolver
            providers["kite"] = kite_provider
        elif kite_rl is not None and secrets_manager is None:
            logger.warning(
                "Kite provider configured but no SecretsManager supplied; Kite provider disabled "
                "for this HistoricalDataEngine instance."
            )

        return providers

    def _provider_order(self) -> list[DataProvider]:
        order = []
        for name in (self._config.primary_provider, self._config.secondary_provider):
            provider = self._providers.get(name)
            if provider is not None and provider not in order:
                order.append(provider)
        return order

    def _fetch_ohlcv_with_fallback(self, symbol: str, start: date, end: date) -> tuple[list[OHLCVBar], str]:
        for provider in self._provider_order():
            try:
                bars = provider.fetch_ohlcv(symbol, start, end)
            except DataProviderError as exc:
                logger.warning("Provider '%s' failed for %s: %s. Trying next provider.", provider.name, symbol, exc)
                continue
            if bars:
                return bars, provider.name
            logger.warning("Provider '%s' returned no rows for %s. Trying next provider.", provider.name, symbol)
        return [], "none"

    def _fetch_corporate_actions_with_fallback(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        for provider in self._provider_order():
            try:
                actions = provider.fetch_corporate_actions(symbol, start, end)
                if actions:
                    return actions
            except DataProviderError as exc:
                logger.warning(
                    "Provider '%s' failed fetching corporate actions for %s: %s", provider.name, symbol, exc
                )
        return []

    def ingest(
        self,
        symbols: list[str],
        start: date,
        end: date,
        *,
        force: bool = False,
        force_reason: str | None = None,
    ) -> DataSnapshot:
        """Ingest OHLCV + corporate actions for `symbols` over [start, end],
        validate, persist as a new immutable snapshot, and register it.

        Raises CriticalDataQualityError (halting the whole ingestion run,
        not just the offending symbol — a run should not be registered as a
        usable snapshot if part of it is known-bad) unless `force=True` is
        passed with `force_reason`, per Phase 1 §1.4 fail-safe default.
        """
        snapshot_id = generate_snapshot_id()
        run_id = self._registry.start_ingestion_run(snapshot_id)
        logger.info("Starting ingestion run %d for snapshot '%s': %s", run_id, snapshot_id, symbols)

        total_rows = 0
        used_providers: set[str] = set()

        try:
            for symbol in symbols:
                bars, provider_name = self._fetch_ohlcv_with_fallback(symbol, start, end)
                actions = self._fetch_corporate_actions_with_fallback(symbol, start, end)

                # Validation happens before persistence — nothing is written
                # for a symbol until it passes the gate (or is force-overridden).
                self._validator.validate(
                    snapshot_id, symbol, bars, actions, start, end,
                    force=force, force_reason=force_reason,
                )

                self._store.write_ohlcv(snapshot_id, symbol, bars)
                self._store.write_corporate_actions(snapshot_id, symbol, actions)
                total_rows += len(bars)
                if provider_name != "none":
                    used_providers.add(provider_name)

            snapshot = DataSnapshot(
                snapshot_id=snapshot_id,
                created_at=datetime.now(timezone.utc),
                symbols=tuple(symbols),
                start_date=start,
                end_date=end,
                source_providers=tuple(sorted(used_providers)),
                row_count=total_rows,
            )
            self._registry.register_snapshot(snapshot)
            self._registry.finish_ingestion_run(run_id, "succeeded")
            logger.info("Ingestion run %d succeeded: snapshot '%s', %d total rows.", run_id, snapshot_id, total_rows)
            return snapshot

        except CriticalDataQualityError as exc:
            self._registry.finish_ingestion_run(run_id, "halted_critical", str(exc))
            logger.error("Ingestion run %d halted on critical data quality issue: %s", run_id, exc)
            raise
        except Exception as exc:  # noqa: BLE001 — re-raised after recording; broad by design here
            self._registry.finish_ingestion_run(run_id, "failed", str(exc))
            logger.exception("Ingestion run %d failed unexpectedly.", run_id)
            raise

    def get_ohlcv(
        self, symbols: list[str], start: date, end: date, snapshot_id: str | None = None
    ) -> dict[str, list[OHLCVBar]]:
        sid = snapshot_id or self._registry.latest_snapshot_id()
        if sid is None:
            raise SnapshotNotFoundError("No snapshots exist yet. Call ingest() before get_ohlcv().")
        return {symbol: self._store.read_ohlcv(sid, symbol, start, end) for symbol in symbols}

    def get_corporate_actions(self, symbol: str, snapshot_id: str | None = None) -> list[CorporateAction]:
        sid = snapshot_id or self._registry.latest_snapshot_id()
        if sid is None:
            raise SnapshotNotFoundError("No snapshots exist yet. Call ingest() before get_corporate_actions().")
        return self._store.read_corporate_actions(sid, symbol)

    def get_instrument_master(self) -> list[InstrumentMeta]:
        """Best-effort merge across configured providers — Kite is preferred
        when available since it carries instrument_token (needed elsewhere),
        NSE is used to fill in anything Kite's response is missing."""
        merged: dict[str, InstrumentMeta] = {}
        for provider in self._provider_order():
            try:
                for instrument in provider.fetch_instrument_master():
                    merged.setdefault(instrument.symbol, instrument)
            except DataProviderError as exc:
                logger.warning("Provider '%s' failed fetching instrument master: %s", provider.name, exc)
        return list(merged.values())

    def refresh_symbol_resolver(self) -> list[SymbolChangeEvent]:
        if self._symbol_resolver is None:
            raise RuntimeError(
                "No SymbolResolver configured (Kite provider not built — was a SecretsManager supplied?)."
            )
        return self._symbol_resolver.refresh()

    def close(self) -> None:
        """Release every resource this engine holds: the SQLite snapshot
        registry connection and every provider's HTTP session. Safe to call
        multiple times. Prefer using HistoricalDataEngine as a context
        manager (`with HistoricalDataEngine(...) as engine:`) so this always
        runs even if an exception is raised mid-use — a resource leak on the
        memory-constrained live micro instance is a real operational risk,
        not just untidiness.
        """
        for provider in self._providers.values():
            close_fn = getattr(provider, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:  # noqa: BLE001 — best-effort cleanup, never block shutdown on it
                    logger.warning("Error closing provider '%s' (continuing shutdown).", provider.name, exc_info=True)
        self._registry.close()

    def __enter__(self) -> "HistoricalDataEngine":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
