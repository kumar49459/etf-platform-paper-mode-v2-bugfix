"""Unit tests for reproducibility.py — git commit hash capture, dirty
detection, and the required-snapshot guard."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from etf_platform.backtesting.exceptions import ReproducibilityError
from etf_platform.backtesting.reproducibility import build_reproducibility_record, get_code_version


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


class TestGetCodeVersion(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_non_git_directory_returns_unknown_and_dirty(self) -> None:
        commit_hash, is_dirty = get_code_version(self.tmp_dir)
        self.assertEqual(commit_hash, "unknown")
        self.assertTrue(is_dirty)

    def test_clean_git_repo_returns_commit_hash_not_dirty(self) -> None:
        _run(["git", "init"], self.tmp_dir)
        _run(["git", "config", "user.email", "test@test.com"], self.tmp_dir)
        _run(["git", "config", "user.name", "test"], self.tmp_dir)
        (self.tmp_dir / "file.txt").write_text("hello")
        _run(["git", "add", "."], self.tmp_dir)
        _run(["git", "commit", "-m", "initial"], self.tmp_dir)

        commit_hash, is_dirty = get_code_version(self.tmp_dir)
        self.assertNotEqual(commit_hash, "unknown")
        self.assertEqual(len(commit_hash), 40)
        self.assertFalse(is_dirty)

    def test_dirty_repo_detected(self) -> None:
        _run(["git", "init"], self.tmp_dir)
        _run(["git", "config", "user.email", "test@test.com"], self.tmp_dir)
        _run(["git", "config", "user.name", "test"], self.tmp_dir)
        (self.tmp_dir / "file.txt").write_text("hello")
        _run(["git", "add", "."], self.tmp_dir)
        _run(["git", "commit", "-m", "initial"], self.tmp_dir)

        (self.tmp_dir / "file.txt").write_text("modified, not committed")
        commit_hash, is_dirty = get_code_version(self.tmp_dir)
        self.assertTrue(is_dirty)


class TestBuildReproducibilityRecord(unittest.TestCase):
    def test_missing_snapshot_id_raises_by_default(self) -> None:
        with self.assertRaises(ReproducibilityError):
            build_reproducibility_record(config_version="abc123", data_snapshot_id=None)

    def test_missing_snapshot_id_allowed_when_explicitly_overridden(self) -> None:
        record = build_reproducibility_record(
            config_version="abc123", data_snapshot_id=None, require_data_snapshot=False
        )
        self.assertEqual(record.data_snapshot_id, "none")

    def test_record_carries_all_required_fields(self) -> None:
        record = build_reproducibility_record(config_version="cfg-v1", data_snapshot_id="snap-v1")
        self.assertEqual(record.config_version, "cfg-v1")
        self.assertEqual(record.data_snapshot_id, "snap-v1")
        self.assertTrue(record.run_id.startswith("backtest-"))
        self.assertIsNotNone(record.started_at)


if __name__ == "__main__":
    unittest.main()
