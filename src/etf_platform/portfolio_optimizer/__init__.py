"""Portfolio Optimizer (Phase 5, Phase 1 Module 3).

Public entry point: PortfolioOptimizer. Output is always target weights
(percentages), never amounts or quantities -- the binding capital-agnostic
rule from §15. Importing this package also imports methods/, which
registers the default (inverse-volatility) allocation method.
"""

from etf_platform.portfolio_optimizer import methods  # noqa: F401 -- registers built-in methods
from etf_platform.portfolio_optimizer.exceptions import (
    EmptyCandidateUniverseError,
    MethodNotRegisteredError,
    PortfolioOptimizerError,
)
from etf_platform.portfolio_optimizer.models import (
    OptimizationMethod,
    OptimizationResult,
    TargetWeight,
    WeightComponent,
)
from etf_platform.portfolio_optimizer.optimizer import PortfolioOptimizer
from etf_platform.portfolio_optimizer.proposal_builder import ProposalArtifact, build_proposal

__all__ = [
    "PortfolioOptimizer",
    "OptimizationMethod",
    "OptimizationResult",
    "TargetWeight",
    "WeightComponent",
    "ProposalArtifact",
    "build_proposal",
    "PortfolioOptimizerError",
    "MethodNotRegisteredError",
    "EmptyCandidateUniverseError",
]
