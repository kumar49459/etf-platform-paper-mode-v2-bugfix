"""AIAllocationPort - preparing the architecture for the future AI Dynamic
Allocation Engine (roadmap Phase 7) WITHOUT implementing it, and WITHOUT
modifying frozen Strategy Engine (v0.6).

WHY THIS REQUIRES NO FROZEN-FILE CHANGES: StrategyEngine's frozen
interface already accepts target_weights as an external input -- it was
never computed internally. That means the AI hook point already exists,
implicitly, in the frozen design: whatever calls StrategyEngine can adjust
target_weights before passing them in. AIAllocationPort formalizes that
existing seam rather than opening a new one inside frozen code.

    base_target_weights = {...}  # from Portfolio Optimizer, Phase 5, frozen
    adjusted = ai_port.recommend_adjustment(base_target_weights, as_of_date)
    strategy = StrategyEngine(target_weights=adjusted)  # frozen v0.6, unmodified

This lives in its own tiny package - not inside strategy_engine (which
must never depend on it) and not inside execution_manager (Module 28 must
remain completely independent of AI, and putting an AI-related type
inside Module 28's own package would be a semantic smell even if nothing
there ever imported it). This will be the future home of the real AI
Dynamic Allocation Engine's client-facing contract - today it holds only
the interface and the safe, disabled default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class AIAllocationPort(ABC):
    """Requirement 4: AI may only RECOMMEND allocation adjustments - this
    interface has exactly one method, and its signature makes an execution
    capability structurally impossible: it takes weights and a date, and
    returns weights. There is no order, no broker, no quantity, nothing
    that could execute anything (requirement 5) - not because of a
    promise, but because the return type is a plain weights dict, the same
    shape Strategy Engine already consumes from Portfolio Optimizer today.
    """

    @abstractmethod
    def recommend_adjustment(self, base_target_weights, as_of_date):
        """Returns an adjusted target-weights dict. The real AI Engine
        (not built yet) might, for example, tilt weights toward or away
        from a symbol based on signals Portfolio Optimizer's mean-variance
        approach doesn't capture - but whatever it does, the output is
        just another target_weights dict, consumed identically to a
        human- or Portfolio-Optimizer-produced one by the unmodified,
        frozen StrategyEngine."""


class DisabledAIAllocationPort(AIAllocationPort):
    """Requirement 2: AI disabled by default. This is the default
    implementation - returns base_target_weights completely unchanged,
    every time. This is what makes requirement 3 (Strategy Engine produces
    identical results when AI is disabled) mechanically true rather than
    merely intended: nothing is adjusted, so the exact same dict (a copy,
    to avoid accidental aliasing) flows into Strategy Engine either way."""

    def recommend_adjustment(self, base_target_weights, as_of_date):
        return dict(base_target_weights)


@dataclass(frozen=True)
class AllocationAdjustmentRecord:
    """Optional, for audit/explainability once a real AI implementation
    exists - what changed and why, kept separate from the weights
    themselves so Strategy Engine's input signature never needs to carry
    AI-specific metadata. Not required by DisabledAIAllocationPort, which
    has nothing to explain."""

    symbol: str
    base_weight: float
    adjusted_weight: float
    rationale: str
