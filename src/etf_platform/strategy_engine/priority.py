"""Buy-only diff and priority ordering (PHASE6_Objectives.md sections 4, 5).

DELIBERATE, ACKNOWLEDGED DUPLICATION: this reimplements the same buy-only-
diff logic as Phase 5's portfolio_optimizer.proposal_builder._buy_only_diff.
That function is private (module-internal) inside a FROZEN package Phase 6
is not permitted to modify. Reusing it would require either importing a
private underscore-prefixed function across a package boundary (a fragile,
undocumented dependency) or promoting it to a public API inside a frozen
phase (a change to frozen code with no production-critical defect
justifying it). Neither is acceptable under the standing "do not modify
frozen phases" instruction, so this is a deliberate, disclosed duplication
of a small, simple, heavily-tested function rather than a compromise on
that rule. If Phase 5 is ever reopened for a real defect, promoting the
original to a shared utility would be a reasonable follow-up - not done
here without that trigger.
"""

from __future__ import annotations

from etf_platform.strategy_engine.models import BuyOpportunity


def compute_buy_only_diff(current_weights, target_weights):
    """A target weight below current is NEVER a negative delta here - it's
    simply excluded. This is the literal enforcement point of the manual-
    selling rule for Phase 6, identical in spirit to Phase 5's
    proposal_builder._buy_only_diff (see module docstring for why this
    isn't a direct import)."""
    all_symbols = set(current_weights) | set(target_weights)
    gaps = {}
    for symbol in all_symbols:
        current = current_weights.get(symbol, 0.0)
        target = target_weights.get(symbol, 0.0)
        if target > current + 1e-9:
            gaps[symbol] = target - current
    return gaps


def prioritize_by_gap(current_weights, target_weights):
    """Largest-weight-gap-first (PHASE6_Objectives.md section 5, approved
    default - rejects proportional deployment, see that section for the
    full reasoning: whole-unit-only ETFs mean proportional deployment risks
    funding nothing at all at small capital levels, while largest-gap-first
    is self-correcting and more likely to produce an executable order)."""
    gaps = compute_buy_only_diff(current_weights, target_weights)
    opportunities = [
        BuyOpportunity(
            symbol=symbol, current_weight=current_weights.get(symbol, 0.0),
            target_weight=target_weights.get(symbol, 0.0), gap=gap,
        )
        for symbol, gap in gaps.items()
    ]
    opportunities.sort(key=lambda o: (-o.gap, o.symbol))
    return opportunities
