"""Portfolio Optimizer orchestrator (Phase 5, F1-F7).

Hard-constraint enforcement lives HERE, once, applied uniformly to
whichever AllocationMethod produced the raw weights -- see
methods/base.py's docstring for why this split exists. The algorithm is
iterative proportional capping ("water-filling"): any ETF/asset-class
exceeding its cap is clamped to the cap, and the excess is redistributed
proportionally among the still-uncapped candidates, repeating until stable.
If no uncapped candidate remains to redistribute into, the excess becomes
unallocated cash reserve -- NOT a constraint violation and NOT treated as
infeasibility (per F1: weights may legitimately sum to less than 100%).

A defensive final clamp runs after the iterative loop regardless of
whether it fully converged, so a hard constraint is structurally
impossible to violate in the output even in a pathological edge case the
iteration didn't fully resolve -- correctness is guaranteed by the final
check, not merely by trusting the loop's convergence.
"""

from __future__ import annotations

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer.exceptions import EmptyCandidateUniverseError
from etf_platform.portfolio_optimizer.methods.base import get_method
from etf_platform.portfolio_optimizer.models import OptimizationMethod, OptimizationResult, TargetWeight
from etf_platform.risk_management.engine import RiskManagementEngine
from etf_platform.risk_management.models import HardConstraints

logger = get_logger("portfolio_optimizer.optimizer")


class PortfolioOptimizer:
    def __init__(self, risk_engine: RiskManagementEngine) -> None:
        self._risk_engine = risk_engine

    @staticmethod
    def _first_price_sanity_issue(bars: list[OHLCVBar]) -> str | None:
        """Returns a description of the first structurally invalid bar
        found (non-positive price, or low > high), or None if all bars are
        sane. Mirrors BacktestEngine._validate_bars_sanity's checks (Phase
        4) -- same defensive purpose, applied here per-symbol rather than
        as a whole-batch raise, since Portfolio Optimizer operates over a
        multi-symbol universe where one bad symbol should be excluded, not
        fail the entire optimization.
        """
        for bar in bars:
            if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
                return (
                    f"Non-positive price in OHLCV data on {bar.trade_date} "
                    f"(open={bar.open}, high={bar.high}, low={bar.low}, close={bar.close}). "
                    "This data should have been caught by the Data Quality Validator before "
                    "reaching Portfolio Optimizer; excluded defensively."
                )
            if bar.low > bar.high:
                return (
                    f"Invalid bar on {bar.trade_date}: low ({bar.low}) > high ({bar.high}). "
                    "This data should have been caught by the Data Quality Validator before "
                    "reaching Portfolio Optimizer; excluded defensively."
                )
        return None

    def optimize(
        self,
        candidates: list[ETFScore],
        asset_class_by_symbol: dict[str, str | None],
        price_history: dict[str, list[OHLCVBar]],
        current_holdings: dict[str, float] | None = None,
        method: OptimizationMethod = OptimizationMethod.INVERSE_VOLATILITY,
    ) -> OptimizationResult:
        if not candidates:
            raise EmptyCandidateUniverseError(
                "Portfolio Optimizer received an empty candidate universe -- there is nothing to "
                "optimize over. This is refused explicitly, not silently returned as an empty result."
            )

        current_holdings = current_holdings or {}
        constraints = self._risk_engine.get_constraints()
        hard = constraints.hard

        eligible: list[ETFScore] = []
        excluded: list[tuple[str, str]] = []
        for score in candidates:
            bars = price_history.get(score.symbol, [])
            sanity_issue = self._first_price_sanity_issue(bars)
            if sanity_issue is not None:
                # Found via adversarial testing (see CHANGELOG.md): a single
                # corrupted bar (non-positive price, or low > high) that
                # somehow bypassed the Data Quality Validator was silently
                # tolerated here -- the volatility calculation just dropped
                # the resulting non-finite return with no signal that
                # anything was wrong. Phase 4's BacktestEngine already
                # guards against exactly this (_validate_bars_sanity); this
                # is the same defensive gate applied here, symbol-by-symbol
                # rather than as a whole-batch failure, since one bad
                # symbol shouldn't invalidate an entire universe the way it
                # would a single fixed backtest.
                excluded.append((score.symbol, sanity_issue))
            elif len(bars) < hard.min_history_days_required:
                excluded.append((
                    score.symbol,
                    f"Only {len(bars)} days of price history; minimum required is "
                    f"{hard.min_history_days_required} (data-quality hard constraint).",
                ))
            else:
                eligible.append(score)

        if not eligible:
            return OptimizationResult(
                feasible=False, excluded_symbols=tuple(excluded),
                infeasibility_reason="No candidate ETF has sufficient price history to compute an allocation.",
            )

        allocation_method = get_method(method)
        raw_result = allocation_method.compute_raw_weights(
            eligible, price_history, current_holdings, constraints.soft
        )

        method_excluded = {s.symbol for s in eligible} - set(raw_result)
        for symbol in method_excluded:
            excluded.append((
                symbol,
                "The selected allocation method could not compute a weight for this ETF "
                "(e.g. insufficient or degenerate volatility estimate).",
            ))

        if not raw_result:
            return OptimizationResult(
                feasible=False, excluded_symbols=tuple(excluded),
                infeasibility_reason=f"No candidate ETF produced a valid raw weight from method '{method.value}'.",
            )

        capped_weights, cap_notes = self._apply_hard_caps(raw_result, asset_class_by_symbol, hard)

        target_weights = []
        for symbol, weight in capped_weights.items():
            if weight <= 1e-9:
                continue
            _, components = raw_result[symbol]
            was_capped = symbol in cap_notes
            target_weights.append(TargetWeight(
                symbol=symbol, weight=weight, method_used=method, components=tuple(components),
                was_capped=was_capped, cap_reason=cap_notes.get(symbol, ""),
            ))

        total_invested = sum(tw.weight for tw in target_weights)
        cash_reserve = max(0.0, 1.0 - total_invested)

        logger.info(
            "Optimization complete: method=%s, %d target weights, %d excluded, cash_reserve=%.2f%%",
            method.value, len(target_weights), len(excluded), cash_reserve * 100,
        )

        return OptimizationResult(
            feasible=True, target_weights=tuple(target_weights), excluded_symbols=tuple(excluded),
            cash_reserve_pct=cash_reserve, method_used=method,
        )

    @staticmethod
    def _apply_hard_caps(
        raw_result: dict[str, tuple[float, list]],
        asset_class_by_symbol: dict[str, str | None],
        hard: HardConstraints,
    ) -> tuple[dict[str, float], dict[str, str]]:
        """Iterative proportional capping ("water-filling") — see module
        docstring. Two independent constraint types (per-ETF, per-asset-
        class) are enforced in the same loop.

        Critical correctness point, found via adversarial testing (see
        CHANGELOG.md): a symbol already at its per-ETF cap must still be
        eligible for asset-class scale-DOWN — being "locked" only ever
        means "cannot be pushed back up by redistribution," never "cannot
        be reduced by a different constraint." An earlier version of this
        method excluded per-ETF-capped symbols from asset-class scaling
        entirely, which meant three ETFs each individually within a 15%
        per-ETF cap could sum to 45% in the same asset class with nothing
        catching it. Scaling a weight down never violates a smaller
        upper-bound constraint, so there was never a reason to exclude it.
        """
        weights = {s: w for s, (w, _) in raw_result.items()}
        cap_notes: dict[str, str] = {}

        max_iterations = len(weights) * 3 + 10
        for _ in range(max_iterations):
            changed = False

            # --- Per-ETF cap: clip anything over, track excess ---
            excess = 0.0
            over_cap = []
            for symbol, w in weights.items():
                if w > hard.max_weight_per_etf + 1e-9:
                    excess += w - hard.max_weight_per_etf
                    weights[symbol] = hard.max_weight_per_etf
                    over_cap.append(symbol)
                    changed = True
            for symbol in over_cap:
                cap_notes[symbol] = cap_notes.get(symbol, "") + f"Capped at per-ETF max {hard.max_weight_per_etf:.1%}. "

            if excess > 1e-9:
                # Redistribute to whoever is currently BELOW their own per-ETF
                # cap — a slight overshoot here is self-correcting, caught by
                # the per-ETF check at the top of the next iteration.
                redistributable = {s: w for s, w in weights.items() if w < hard.max_weight_per_etf - 1e-9}
                total_redist = sum(redistributable.values())
                if total_redist > 1e-9:
                    for symbol in redistributable:
                        weights[symbol] += excess * (weights[symbol] / total_redist)
                # else: no room anywhere -> excess becomes unallocated cash reserve (valid per F1).

            # --- Asset-class cap: scale down ALL members of any over-cap
            # class, regardless of per-ETF-lock status (see docstring). ---
            class_totals: dict[str, float] = {}
            for symbol, w in weights.items():
                ac = asset_class_by_symbol.get(symbol)
                if ac is not None:
                    class_totals[ac] = class_totals.get(ac, 0.0) + w

            class_excess = 0.0
            scaled_this_round: set[str] = set()
            for ac, total in class_totals.items():
                if total > hard.max_weight_per_asset_class + 1e-9:
                    scale = hard.max_weight_per_asset_class / total
                    for symbol, w in list(weights.items()):
                        if asset_class_by_symbol.get(symbol) == ac:
                            new_w = w * scale
                            class_excess += w - new_w
                            weights[symbol] = new_w
                            scaled_this_round.add(symbol)
                            cap_notes[symbol] = cap_notes.get(symbol, "") + (
                                f"Scaled down for asset-class max {hard.max_weight_per_asset_class:.1%} on '{ac}'. "
                            )
                            changed = True

            if class_excess > 1e-9:
                # Redistribute only to symbols NOT in a class that was just
                # scaled down (redistributing into the same class would
                # immediately re-violate the cap we just enforced) and
                # currently below their own per-ETF cap.
                eligible = {
                    s: w for s, w in weights.items()
                    if s not in scaled_this_round and w < hard.max_weight_per_etf - 1e-9
                }
                total_eligible = sum(eligible.values())
                if total_eligible > 1e-9:
                    for symbol in eligible:
                        weights[symbol] += class_excess * (weights[symbol] / total_eligible)

            if not changed:
                break

        # Final defensive clamp — runs regardless of whether the loop fully
        # converged, so BOTH constraint types are structurally impossible to
        # violate in the output, not merely "very likely satisfied." This
        # clamp intentionally does NOT redistribute the shortfall it creates
        # (that would risk re-violating the very cap it's enforcing) — any
        # gap it introduces becomes cash reserve, which is always valid.
        for symbol, w in list(weights.items()):
            if w > hard.max_weight_per_etf + 1e-6:
                weights[symbol] = hard.max_weight_per_etf
                cap_notes[symbol] = cap_notes.get(symbol, "") + "Final defensive per-ETF clamp applied. "

        class_totals = {}
        for symbol, w in weights.items():
            ac = asset_class_by_symbol.get(symbol)
            if ac is not None:
                class_totals[ac] = class_totals.get(ac, 0.0) + w
        for ac, total in class_totals.items():
            if total > hard.max_weight_per_asset_class + 1e-6:
                scale = hard.max_weight_per_asset_class / total
                for symbol, w in list(weights.items()):
                    if asset_class_by_symbol.get(symbol) == ac:
                        weights[symbol] = w * scale
                        cap_notes[symbol] = cap_notes.get(symbol, "") + "Final defensive asset-class clamp applied. "

        return weights, cap_notes
