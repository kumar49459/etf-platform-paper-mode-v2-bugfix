"""Risk Management Engine models (Phase 5).

Constraints are explicitly partitioned into HARD and SOFT per the binding
decision recorded in PHASE5_Objectives.md:
  HARD = regulatory limits, capital protection, risk limits, the
  manual-selling rule, approval workflow, compliance, data quality, kill
  switch. A hard constraint is never violated in Portfolio Optimizer's
  output -- violating it means "infeasible," not "penalized."
  SOFT = optimization-objective preferences only. A soft constraint nudges
  a method's solution when there's genuine freedom to choose (e.g., a
  future multi-objective method breaking ties) but never blocks a result.

max_drawdown_target is deliberately NOT enforced inside Portfolio
Optimizer's weight formula -- see risk_management/engine.py's module
docstring for why (drawdown is a property of estimated future behavior,
only knowable via backtesting, not something inverse-volatility or any
formula-based method can solve for directly). It is still a HARD
constraint in the sense that a proposal violating it can never be marked
approval-ready without going through the "present both options" flow
(F11) -- enforcement happens at the proposal-validation stage, not the
weight-computation stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

_SELL_TRIGGER_WORDS = {
    "sell", "sells", "selling", "sold",
    "liquidate", "liquidates", "liquidating", "liquidated",
}
_SELL_TRIGGER_PHRASES = ("reduce position by", "execute sale")
_NEGATION_WORDS = {"no", "not", "never", "wont", "cannot", "cant", "without", "isnt", "arent", "doesnt"}
# Deliberately narrow (2, not the original 4): a wide lookback window let an
# unrelated negation word earlier in the sentence "shield" an actual sell
# instruction (e.g. "no no no you should actually sell this" -- found via
# adversarial testing, see CHANGELOG.md). A narrow window trades a few
# false positives (legitimate text blocked because a negation word is a
# little too far away) for far fewer false negatives (an actual sell
# instruction slipping through) -- the right tradeoff given what this
# guard protects. Every legitimate disclosure phrase this platform
# actually uses ("no sell will be proposed", "will never sell") places the
# negation word immediately adjacent to the trigger, well within window=2.
_NEGATION_WINDOW = 2


def _flag_sell_like_instruction(text: str) -> None:
    """Raises ManualSellingViolationError if `text` contains an actual sell
    instruction, not just a mention of "sell" in a negated disclosure
    sentence like "no sell will be proposed." Tokenizes on word boundaries
    and strips punctuation/apostrophes so "won't" and "wont" match the same
    negation-word check.

    HONEST LIMITATION: this is a heuristic defense against ACCIDENTAL sell
    language from a code bug (e.g. a future change generating "consider
    selling X" without realizing that violates the manual-selling rule) --
    it is not, and cannot be, a bulletproof filter against deliberately
    adversarial text. recommended_action strings are generated entirely by
    this platform's own code, never from external/user input, so the
    realistic threat model is "our own code accidentally says something
    sell-like," not "an adversary crafts obfuscated text to smuggle a sell
    instruction past this check." Defending against the latter (e.g.
    "S.E.L.L the position") would require much more sophisticated NLP and
    risks new false positives elsewhere, for a threat this guard was never
    designed to stop -- the real defense against that threat is that no
    external input reaches this constructor at all.
    """
    words = re.findall(r"[a-zA-Z]+", text.lower())

    for i, word in enumerate(words):
        if word in _SELL_TRIGGER_WORDS:
            window = words[max(0, i - _NEGATION_WINDOW):i]
            if any(neg in window for neg in _NEGATION_WORDS):
                continue
            _raise_violation(text, word)

    lowered_no_punct = " ".join(words)
    for phrase in _SELL_TRIGGER_PHRASES:
        phrase_words = phrase.split()
        if " ".join(phrase_words) in lowered_no_punct:
            idx = lowered_no_punct.split().index(phrase_words[0]) if phrase_words[0] in lowered_no_punct.split() else -1
            window = words[max(0, idx - _NEGATION_WINDOW):idx] if idx >= 0 else []
            if any(neg in window for neg in _NEGATION_WORDS):
                continue
            _raise_violation(text, phrase)


def _raise_violation(text: str, trigger: str) -> None:
    from etf_platform.risk_management.exceptions import ManualSellingViolationError

    raise ManualSellingViolationError(
        f"RiskEvent recommended_action contains an unnegated sell-instruction trigger ('{trigger}'): "
        f"{text!r}. Risk Management Engine may recommend protective, non-sell actions only -- "
        "selling is 100% manual, per the binding decision recorded in PHASE5_Objectives.md."
    )


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RiskEventType(str, Enum):
    BREACH_MAX_WEIGHT_PER_ETF = "breach_max_weight_per_etf"
    BREACH_MAX_WEIGHT_PER_ASSET_CLASS = "breach_max_weight_per_asset_class"
    BREACH_MAX_DRAWDOWN = "breach_max_drawdown"
    ALLOCATION_DRIFT = "allocation_drift"
    KILL_SWITCH_REQUESTED = "kill_switch_requested"


@dataclass(frozen=True)
class HardConstraints:
    """Constraints that Portfolio Optimizer's output must never violate.
    Every default below is a provisional, disclosed placeholder pending
    your review -- none of these numbers come from a source I can cite the
    way the Cost & Tax Engine's tax rates were cited; they are reasonable
    starting points for a personal ETF portfolio, not researched optimal
    values. Override them explicitly before relying on the defaults for a
    real allocation decision.
    """

    max_weight_per_etf: float = 0.40
    max_weight_per_asset_class: float = 0.60
    max_drawdown_target: float = 0.20  # upper end of the already-approved 15-20% band, §12.2
    min_history_days_required: int = 200  # an ETF with less history than this is excluded, not silently included
    allow_manual_sell_override: bool = True  # the ONLY path by which a sell can ever occur -- see decision 1
    drift_tolerance_pct: float = 0.05
    """Provisional, per decision #4 (PHASE5_Objectives.md) -- needs future
    backtesting/walk-forward validation, not a researched value. Moved here
    (rather than a bare RiskManagementEngine constructor kwarg, which is
    how it originally shipped) after an adversarial-review finding: an
    unvalidated drift tolerance let a negative value produce false-positive
    drift alerts even with zero actual drift, and a value above 1.0
    silently disabled drift detection entirely (no amount of drift could
    ever exceed it). Living inside HardConstraints means it now goes
    through the same validate() every other numeric constraint does,
    consistent with F8's own requirement that constraints be "structured,
    versioned config -- not scattered magic numbers." See CHANGELOG.md.
    """

    def validate(self) -> list[str]:
        errors = []
        if not (0 < self.max_weight_per_etf <= 1.0):
            errors.append(f"max_weight_per_etf must be in (0, 1], got {self.max_weight_per_etf}")
        if not (0 < self.max_weight_per_asset_class <= 1.0):
            errors.append(f"max_weight_per_asset_class must be in (0, 1], got {self.max_weight_per_asset_class}")
        if self.max_weight_per_etf > self.max_weight_per_asset_class:
            errors.append(
                "max_weight_per_etf cannot exceed max_weight_per_asset_class "
                f"({self.max_weight_per_etf} > {self.max_weight_per_asset_class}) -- "
                "a single ETF cannot be allowed to exceed its own asset class's cap."
            )
        if not (0 < self.max_drawdown_target <= 1.0):
            errors.append(f"max_drawdown_target must be in (0, 1], got {self.max_drawdown_target}")
        if self.min_history_days_required <= 0:
            errors.append(f"min_history_days_required must be > 0, got {self.min_history_days_required}")
        if not (0 < self.drift_tolerance_pct <= 1.0):
            errors.append(f"drift_tolerance_pct must be in (0, 1], got {self.drift_tolerance_pct}")
        return errors


@dataclass(frozen=True)
class SoftPreferences:
    """Optimization-objective preferences only -- never block a result, only
    influence it when genuine freedom of choice exists. Phase 5's default
    method (inverse-volatility) is a closed-form formula with no ties to
    break, so these are currently inert for the default method; they exist
    so future pluggable methods (Risk Parity, Black-Litterman, HRP -- see
    methods/base.py) that DO face multi-solution trade-offs have a defined
    place to read preferences from, without requiring a redesign of this
    model when they're added.
    """

    prefer_lower_correlation_to_holdings: bool = True
    prefer_higher_liquidity: bool = True


@dataclass(frozen=True)
class RiskConstraints:
    hard: HardConstraints = field(default_factory=HardConstraints)
    soft: SoftPreferences = field(default_factory=SoftPreferences)

    def validate(self) -> None:
        from etf_platform.risk_management.exceptions import InvalidConstraintsError

        errors = self.hard.validate()
        if errors:
            raise InvalidConstraintsError(
                "RiskConstraints failed validation with the following error(s):\n  - " + "\n  - ".join(errors)
            )


@dataclass(frozen=True)
class RiskEvent:
    event_id: str
    timestamp: datetime
    event_type: RiskEventType
    severity: Severity
    description: str
    recommended_action: str
    symbol: str | None = None

    def __post_init__(self) -> None:
        # Structural enforcement of the manual-selling rule at the model
        # level, not just by convention: a RiskEvent's recommended_action
        # must never read as an actual sell instruction. This is a
        # best-effort textual guard (not a substitute for the architectural
        # boundary enforced by which code paths can even construct a
        # RiskEvent) but it's a cheap, meaningful defense-in-depth check.
        #
        # Negation-aware: a naive substring match on "sell" would flag the
        # engine's OWN correct disclosure language ("no sell will be
        # proposed") as if it were a sell instruction -- found by testing
        # this immediately after writing it, not left as a false positive.
        # A trigger word is only flagged if it's not preceded within a
        # short window by a negation marker.
        _flag_sell_like_instruction(self.recommended_action)
