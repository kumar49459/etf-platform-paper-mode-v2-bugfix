"""Entry point: `python -m etf_platform.main`.

Selects PaperRunner or ProductionRunner based on the resolved environment
-- 'paper' (or 'dev', treated identically to paper for safety) uses
PaperRunner; 'live' or 'production' uses ProductionRunner. Everything
else is refused explicitly rather than guessed.

This file makes the selection ONLY. Both PaperRunner and ProductionRunner
already register their own SIGTERM/SIGINT handlers and expose
shutdown_requested() -- main.py's only job is to pick the class and
drive the loop.
"""

from __future__ import annotations

import os
import sys
import time

from etf_platform.common.logging_setup import get_logger

logger = get_logger("etf_platform.main")

PAPER_ENVIRONMENTS = frozenset({"paper", "dev"})
LIVE_ENVIRONMENTS = frozenset({"live", "production"})


def resolve_environment():
    return os.environ.get("ETF_PLATFORM_ENV", "dev")


def select_runner_class(environment):
    if environment in PAPER_ENVIRONMENTS:
        from etf_platform.paper_trading_operations.paper_runner import PaperRunner
        return PaperRunner
    if environment in LIVE_ENVIRONMENTS:
        from etf_platform.production.production_runner import ProductionRunner
        return ProductionRunner
    raise ValueError(
        f"Unrecognized environment {environment!r} (from ETF_PLATFORM_ENV). "
        f"Must be one of {sorted(PAPER_ENVIRONMENTS)} (paper trading, no real broker) "
        f"or {sorted(LIVE_ENVIRONMENTS)} (real Kite account, real money). "
        f"Refusing to guess which one you meant."
    )


def main():
    environment = resolve_environment()
    runner_class = select_runner_class(environment)
    logger.info("Selected %s for environment=%r.", runner_class.__name__, environment)

    if runner_class.__name__ == "ProductionRunner":
        logger.warning(
            "LIVE MODE SELECTED (environment=%r). This will use real Kite credentials "
            "and may place real orders with real money.", environment,
        )

    runner = runner_class(config_dir="config", environment=environment)
    runner.startup()

    try:
        while not runner.shutdown_requested():
            time.sleep(1)
    finally:
        runner.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
