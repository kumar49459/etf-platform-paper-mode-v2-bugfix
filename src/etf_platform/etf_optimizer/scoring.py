"""ETF Scoring Engine (Phase 3) — the core of the "ETF Universe Optimizer".

Computes 8 metrics per ETF, exactly as specified:
  liquidity, AUM, expense ratio, tracking error, trading volume, volatility,
  correlation, diversification.

Methodology (documented here once, since every design choice below directly
determines what "ranked #1" means — see PHASE1 §5.3's rejection of
mean-variance optimization for the same "avoid overfitting to noise on a
small universe" rationale that applies here too):

- **Z-score normalization within the screened, scored universe** — not
  against some external absolute scale. A metric only means something
  relative to the other candidates actually being compared.
- **Direction-aware**: liquidity/AUM/trading_volume are higher-is-better;
  expense_ratio/tracking_error/volatility are lower-is-better (this
  platform's stated objective is validated XIRR *with* drawdown control,
  not raw volatility-chasing — see PHASE1 §12.2); correlation is
  lower-is-better (less correlated to current holdings = more diversifying);
  diversification is higher-is-better by construction.
- **Missing data contributes exactly 0 to the composite**, not a penalty
  and not an exclusion-with-reweighting. Reweighting per-ETF based on which
  metrics happen to be available would make scores incomparable across ETFs
  with different missing-data patterns — a methodologically worse failure
  mode than a metric being neutral. Every score's breakdown shows exactly
  which metrics contributed 0 and why (see MetricScore.note).
- **Equal weights (1/8 each) by default.** This is a deliberate, disclosed
  choice, not a claim that all 8 dimensions are equally important in some
  objective sense — nobody can defend a specific unequal weighting without
  it being an opinion about investment philosophy, which this platform
  should not silently embed as fact. Pass `weights=` to override; the
  default is transparency, not a recommendation.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer import price_metrics
from etf_platform.etf_optimizer.models import ETFMetadata, ETFScore, MetricScore

logger = get_logger("etf_optimizer.scoring")

METRIC_DIRECTIONS: dict[str, str] = {
    "liquidity": "higher_is_better",
    "aum": "higher_is_better",
    "expense_ratio": "lower_is_better",
    "tracking_error": "lower_is_better",
    "trading_volume": "higher_is_better",
    "volatility": "lower_is_better",
    "correlation": "lower_is_better",
    "diversification": "higher_is_better",
}

DEFAULT_WEIGHTS: dict[str, float] = {name: 1.0 / len(METRIC_DIRECTIONS) for name in METRIC_DIRECTIONS}


def _portfolio_aggregate_returns(
    holdings_bars: dict[str, list[OHLCVBar]],
) -> dict[date, float]:
    """Equal-weighted average daily return across current holdings, aligned
    on dates where at least one holding has a return. Used as the reference
    series for the correlation metric. Equal-weighting is a simplification
    (real portfolio weights aren't necessarily equal) — documented rather
    than silently assumed; Phase 5's Portfolio Optimizer is where real
    target weights get computed, this is just a screening-time reference.
    """
    per_symbol_returns: dict[str, dict[date, float]] = {}
    for symbol, bars in holdings_bars.items():
        sorted_bars = sorted(bars, key=lambda b: b.trade_date)
        returns = {}
        for prev, curr in zip(sorted_bars, sorted_bars[1:]):
            if prev.close > 0:
                returns[curr.trade_date] = (curr.close - prev.close) / prev.close
        per_symbol_returns[symbol] = returns

    all_dates = sorted(set().union(*[set(r) for r in per_symbol_returns.values()])) if per_symbol_returns else []
    aggregate: dict[date, float] = {}
    for d in all_dates:
        values = [r[d] for r in per_symbol_returns.values() if d in r]
        if values:
            aggregate[d] = float(np.mean(values))
    return aggregate


class ETFScorer:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights = weights or DEFAULT_WEIGHTS
        unknown = set(self._weights) - set(METRIC_DIRECTIONS)
        if unknown:
            raise ValueError(f"Unknown metric name(s) in weights: {unknown}. Valid: {sorted(METRIC_DIRECTIONS)}")

    def _compute_raw_metrics(
        self,
        symbol: str,
        bars: list[OHLCVBar],
        metadata: ETFMetadata,
        portfolio_returns: dict[date, float],
        current_holdings_asset_classes: set[str],
    ) -> dict[str, float | None]:
        raw: dict[str, float | None] = {
            "liquidity": price_metrics.average_daily_turnover_inr(bars),
            "aum": metadata.aum_crores,
            "expense_ratio": metadata.expense_ratio,
            "tracking_error": metadata.tracking_error_pct,
            "trading_volume": price_metrics.average_daily_volume(bars),
            "volatility": price_metrics.annualized_volatility(bars),
        }

        # Correlation: this ETF's returns vs the current-holdings aggregate.
        sorted_bars = sorted(bars, key=lambda b: b.trade_date)
        symbol_returns = {
            curr.trade_date: (curr.close - prev.close) / prev.close
            for prev, curr in zip(sorted_bars, sorted_bars[1:])
            if prev.close > 0
        }
        common_dates = sorted(set(symbol_returns) & set(portfolio_returns))
        if len(common_dates) >= 2:
            a = np.array([symbol_returns[d] for d in common_dates])
            b = np.array([portfolio_returns[d] for d in common_dates])
            if np.std(a) > 0 and np.std(b) > 0:
                raw["correlation"] = float(np.corrcoef(a, b)[0, 1])
            else:
                raw["correlation"] = None
        else:
            raw["correlation"] = None

        # Diversification: fraction of current holdings whose asset_class
        # differs from this candidate's. Undefined if we don't know this
        # candidate's asset_class, or there are no current holdings to
        # compare against.
        if metadata.asset_class is None or not current_holdings_asset_classes:
            raw["diversification"] = None
        else:
            distinct = sum(1 for ac in current_holdings_asset_classes if ac != metadata.asset_class)
            raw["diversification"] = distinct / len(current_holdings_asset_classes)

        return raw

    @staticmethod
    def _z_scores(raw_values: dict[str, float | None]) -> dict[str, float | None]:
        """Z-score one metric's raw values across all candidates that have
        a value for it. Candidates missing the value get None (handled as
        zero-contribution by the caller, not by this function — this keeps
        the statistical computation and the "missing = neutral" policy
        decision visibly separate)."""
        valid = {sym: v for sym, v in raw_values.items() if v is not None}
        if len(valid) < 2:
            return {sym: None for sym in raw_values}
        values = np.array(list(valid.values()))
        mean, std = float(np.mean(values)), float(np.std(values, ddof=1))
        if std == 0:
            return {sym: 0.0 if sym in valid else None for sym in raw_values}
        return {sym: (float((v - mean) / std) if v is not None else None) for sym, v in raw_values.items()}

    def score_universe(
        self,
        candidates: dict[str, list[OHLCVBar]],
        metadata: dict[str, ETFMetadata],
        current_holdings_bars: dict[str, list[OHLCVBar]] | None = None,
        current_holdings_metadata: dict[str, ETFMetadata] | None = None,
    ) -> list[ETFScore]:
        """Score every symbol in `candidates` (already screened — this
        method does not screen). `current_holdings_bars`/`_metadata` provide
        the reference for the correlation and diversification metrics; if
        omitted, those two metrics are UNKNOWN for every candidate (there's
        nothing to compare against)."""
        current_holdings_bars = current_holdings_bars or {}
        current_holdings_metadata = current_holdings_metadata or {}

        portfolio_returns = _portfolio_aggregate_returns(current_holdings_bars)
        current_asset_classes = {
            m.asset_class for m in current_holdings_metadata.values() if m.asset_class is not None
        }

        raw_by_metric: dict[str, dict[str, float | None]] = {name: {} for name in METRIC_DIRECTIONS}
        for symbol, bars in candidates.items():
            symbol_meta = metadata[symbol]
            raw = self._compute_raw_metrics(symbol, bars, symbol_meta, portfolio_returns, current_asset_classes)
            for metric_name, value in raw.items():
                raw_by_metric[metric_name][symbol] = value

        z_by_metric = {name: self._z_scores(values) for name, values in raw_by_metric.items()}

        scores: list[ETFScore] = []
        for symbol in candidates:
            metric_scores: list[MetricScore] = []
            composite = 0.0
            for metric_name, direction in METRIC_DIRECTIONS.items():
                weight = self._weights.get(metric_name, 0.0)
                raw_value = raw_by_metric[metric_name][symbol]
                z = z_by_metric[metric_name][symbol]
                if z is None:
                    contribution = 0.0
                    note = "excluded: data unavailable for this ETF or universe-wide for this metric"
                else:
                    signed_z = z if direction == "higher_is_better" else -z
                    contribution = signed_z * weight
                    note = ""
                composite += contribution
                metric_scores.append(
                    MetricScore(
                        metric_name=metric_name, raw_value=raw_value, z_score=z, weight=weight,
                        contribution=contribution, direction=direction, note=note,
                    )
                )
            scores.append(ETFScore(symbol=symbol, composite_score=composite, metric_scores=tuple(metric_scores)))

        scores.sort(key=lambda s: s.composite_score, reverse=True)
        ranked = [
            ETFScore(symbol=s.symbol, composite_score=s.composite_score, metric_scores=s.metric_scores, rank=i + 1)
            for i, s in enumerate(scores)
        ]
        logger.info("Scored %d ETFs. Top-ranked: %s", len(ranked), ranked[0].symbol if ranked else "none")
        return ranked
