"""Reproducibility metadata capture (Phase 4 objective #9; Phase 1 §1.4).

Every backtest run captures exactly three things needed to answer "what
produced this result, precisely":
  1. Code version — git commit hash of the repository at run time.
  2. Config version — the `config_version` hash already computed by
     ConfigManager (Phase 2), reused as-is.
  3. Data snapshot id — the `snapshot_id` already assigned by
     HistoricalDataEngine (Phase 2), reused as-is.

Nothing new was invented for #2 and #3 — this module is almost entirely
composition of Phase 2 machinery that already existed for exactly this
purpose, plus the one genuinely new piece (#1, git integration).
"""

from __future__ import annotations

import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from etf_platform.backtesting.exceptions import ReproducibilityError
from etf_platform.backtesting.models import ReproducibilityRecord
from etf_platform.common.logging_setup import get_logger

logger = get_logger("backtesting.reproducibility")


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def get_code_version(repo_path: str | Path = ".") -> tuple[str, bool]:
    """Returns (commit_hash, is_dirty). If git isn't available or the path
    isn't a repo, returns ("unknown", True) — "unknown" is a valid,
    honestly-reported value; a backtest run with an unknown code version
    should be treated as weakly reproducible by anyone reading the report
    later, not silently reported as if it were fully tracked.
    """
    repo_path = Path(repo_path)
    commit_hash = _run_git(["rev-parse", "HEAD"], repo_path)
    if commit_hash is None:
        logger.warning(
            "Could not determine git commit hash at %s (not a git repo, or git unavailable). "
            "Reporting code_version='unknown' — this backtest's reproducibility is weaker as a result.",
            repo_path,
        )
        return "unknown", True

    status = _run_git(["status", "--porcelain"], repo_path)
    is_dirty = bool(status)
    if is_dirty:
        logger.warning(
            "Uncommitted changes present at backtest run time (commit %s). The exact code that "
            "produced this result is not fully captured by the commit hash alone.",
            commit_hash[:12],
        )
    return commit_hash, is_dirty


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"backtest-{timestamp}-{secrets.token_hex(3)}"


def build_reproducibility_record(
    config_version: str,
    data_snapshot_id: str | None,
    repo_path: str | Path = ".",
    require_data_snapshot: bool = True,
) -> ReproducibilityRecord:
    if require_data_snapshot and not data_snapshot_id:
        raise ReproducibilityError(
            "No data_snapshot_id provided. Per Phase 1 §1.4, a backtest must be tied to an immutable "
            "data snapshot to be reproducible — pass BacktestConfig.snapshot_id, or explicitly set "
            "require_data_snapshot=False if you understand and accept the reduced reproducibility "
            "(e.g. for a quick exploratory run you don't intend to keep)."
        )
    commit_hash, is_dirty = get_code_version(repo_path)
    return ReproducibilityRecord(
        run_id=generate_run_id(),
        code_commit_hash=commit_hash,
        code_is_dirty=is_dirty,
        config_version=config_version,
        data_snapshot_id=data_snapshot_id or "none",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
