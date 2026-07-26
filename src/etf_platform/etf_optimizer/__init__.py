"""ETF Universe Optimizer (Phase 3).

Modules: ETFMetadataManager, UniverseScreeningEngine, ETFUniverseOptimizer
(scoring/ranking), PortfolioCandidateGenerator.

Architectural placement: per PHASE1_Architecture_SRS.md §12.1, this package
belongs on the research/on-demand instance, not the always-on live micro —
it's periodic evaluation work, not real-time trading, and it depends on
numpy/pandas (requirements-research.txt), which the live instance
deliberately does not install. The live trading process must never import
from this package.
"""

from etf_platform.etf_optimizer.candidate_generator import PortfolioCandidateGenerator
from etf_platform.etf_optimizer.metadata_manager import ETFMetadataManager
from etf_platform.etf_optimizer.screening_engine import UniverseScreeningEngine
from etf_platform.etf_optimizer.universe_optimizer import ETFUniverseOptimizer

__all__ = [
    "ETFMetadataManager",
    "UniverseScreeningEngine",
    "ETFUniverseOptimizer",
    "PortfolioCandidateGenerator",
]
