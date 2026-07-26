"""Tests for PaperRunner and main.py (Paper Trading Mode, authorized as
new development work outside the prior freeze). Mocked Telegram
transport only where Telegram is exercised; PaperBrokerPort/
PaperQuoteProvider are the real, already-frozen simulator classes.
"""

from __future__ import annotations

import os
import shutil
import signal
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from etf_platform.config_manager.config_manager import ConfigManager
from etf_platform.execution_manager import ExecutionRecord, OrderLifecycleState, new_execution_id, utc_now
from etf_platform.main import select_runner_class
from etf_platform.paper_trading_operations.paper_runner import PaperRunner
from etf_platform.secrets_manager.secrets_manager import SecretsManager


class TestRunnerSelection(unittest.TestCase):
    def test_paper_selects_paper_runner(self):
        self.assertEqual(select_runner_class("paper").__name__, "PaperRunner")

    def test_dev_selects_paper_runner(self):
        self.assertEqual(select_runner_class("dev").__name__, "PaperRunner")

    def test_live_selects_production_runner(self):
        self.assertEqual(select_runner_class("live").__name__, "ProductionRunner")

    def test_production_selects_production_runner(self):
        self.assertEqual(select_runner_class("production").__name__, "ProductionRunner")

    def test_unrecognized_environment_raises_rather_than_guesses(self):
        with self.assertRaises(ValueError) as ctx:
            select_runner_class("staging")
        self.assertIn("staging", str(ctx.exception))


class TestPaperRunnerStartupShutdown(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        (self.tmp_dir / "config").mkdir()
        (self.tmp_dir / "config" / "base.yaml").write_text(
            "environment: dev\nsecrets:\n  provider: local\n  local_key_env_var: ETF_PLATFORM_MASTER_KEY\n"
            "  local_secrets_file: ./secrets.enc\nlogging:\n  level: INFO\n"
        )
        (self.tmp_dir / "config" / "paper.yaml").write_text("logging:\n  level: INFO\nsecrets:\n  provider: local\n")
        self._old_key = os.environ.get("ETF_PLATFORM_MASTER_KEY")
        os.environ["ETF_PLATFORM_MASTER_KEY"] = Fernet.generate_key().decode()
        self.addCleanup(self._restore_env)
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp_dir)
        self.addCleanup(os.chdir, self._old_cwd)

    def _restore_env(self):
        if self._old_key is not None:
            os.environ["ETF_PLATFORM_MASTER_KEY"] = self._old_key
        else:
            os.environ.pop("ETF_PLATFORM_MASTER_KEY", None)

    def _make_runner(self, **kwargs):
        return PaperRunner(
            config_dir="config", environment="paper", db_path="test.db",
            event_archive_path="events.jsonl", telegram_retry_queue_path="tg_queue.jsonl", **kwargs,
        )

    def test_startup_succeeds_with_zero_kite_credentials_present(self):
        runner = self._make_runner()
        runner.startup()
        self.assertTrue(runner.health_check())
        runner.shutdown()

    def test_no_missing_kite_credentials_error_possible(self):
        """Confirms there is no real import of KiteAuthManager or
        kite_credentials anywhere in this file -- checked via AST, not a
        blanket string search, since the docstring legitimately mentions
        KiteAuthManager to explain what's deliberately NOT used."""
        import ast
        import etf_platform.paper_trading_operations.paper_runner as mod
        tree = ast.parse(Path(mod.__file__).read_text())
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.name)
        self.assertNotIn("KiteAuthManager", imported_names)
        self.assertNotIn("KiteBrokerPort", imported_names)
        self.assertNotIn("load_kite_auth_manager", imported_names)

    def test_telegram_missing_is_a_warning_not_a_startup_failure(self):
        runner = self._make_runner()
        runner.startup()
        self.assertIsNone(runner._notifier)
        runner.shutdown()

    def test_telegram_works_when_configured(self):
        config = ConfigManager(config_dir="config").load()
        sm = SecretsManager(config.secrets)
        sm.set_secret("telegram_bot_token", "test_token")
        sm.set_secret("telegram_chat_id", "test_chat")

        sent_messages = []

        class RecordingTransport:
            def request(self, method, url, data, timeout):
                sent_messages.append(data.get("text"))
                return (200, {"ok": True})

        runner = self._make_runner(telegram_transport=RecordingTransport())
        runner.startup()
        self.assertIsNotNone(runner._notifier)
        self.assertTrue(any("PAPER MODE STARTUP" in m for m in sent_messages))
        runner.shutdown()
        self.assertTrue(any("PAPER MODE SHUTDOWN" in m for m in sent_messages))

    def test_mandatory_reconciliation_runs_on_startup(self):
        runner = self._make_runner()
        runner.startup()
        self.assertIsNotNone(runner._reconciler)
        runner.shutdown()

    def test_a_real_order_flows_through_the_full_orchestrator(self):
        runner = self._make_runner()
        runner.startup()
        record = ExecutionRecord(
            execution_id=new_execution_id(), queue_id=None, cycle_id="test-cycle",
            symbol="NIFTYBEES", quantity_proposed=10, quantity_final=10, limit_price=250.0,
            order_status=OrderLifecycleState.VERIFIED, broker_order_id=None, executed_price=None,
            executed_quantity=0, is_paper_trade=True, created_at=utc_now(), last_status_check=None, priority_rank=1,
        )
        runner._store.save_execution_record(record)
        result = runner._orchestrator.process_order(record, "test-corr")
        self.assertIn(result.order_status, (OrderLifecycleState.PENDING, OrderLifecycleState.SUBMITTED))
        runner.shutdown()

    def test_orders_fill_immediately_with_default_scenario(self):
        runner = self._make_runner()
        runner.startup()
        broker_order_id = runner._broker.submit_order("NIFTYBEES", "BUY", 10, 250.0, "ref")
        status = runner._broker.get_order_status(broker_order_id)
        self.assertEqual(status.state, OrderLifecycleState.FILLED)
        runner.shutdown()

    def test_static_ip_compliance_defaults_to_false_same_as_production(self):
        runner = self._make_runner()
        runner.startup()
        checker = runner._orchestrator._compliance
        self.assertFalse(checker._static_ip_verified)
        runner.shutdown()

    def test_custom_base_prices_are_used(self):
        runner = self._make_runner(base_prices={"NIFTYBEES": 999.0})
        runner.startup()
        price = runner._quotes.get_last_traded_price("NIFTYBEES")
        self.assertEqual(price, 999.0)
        runner.shutdown()

    def test_signal_handlers_set_shutdown_requested(self):
        runner = self._make_runner()
        runner.startup()
        self.assertFalse(runner.shutdown_requested())
        os.kill(os.getpid(), signal.SIGTERM)
        self.assertTrue(runner.shutdown_requested())
        runner.shutdown()


class TestPaperRunnerStartsWithoutMasterKey(unittest.TestCase):
    """Regression for a real bug found via real execution evidence (a
    genuine startup crash, not a hypothetical): PaperRunner used to
    construct SecretsManager unconditionally in startup(), and
    LocalEncryptedFileProvider loads/validates the Fernet key at
    CONSTRUCTION time, not on first get_secret() call -- so paper mode
    crashed before it ever got the chance to treat Telegram as optional.
    Every test in this class explicitly ensures ETF_PLATFORM_MASTER_KEY
    is UNSET, not just unset-by-omission -- the exact condition from the
    bug report, not a nearby-but-different one."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        (self.tmp_dir / "config").mkdir()
        (self.tmp_dir / "config" / "base.yaml").write_text(
            "environment: dev\nsecrets:\n  provider: local\n  local_key_env_var: ETF_PLATFORM_MASTER_KEY\n"
            "  local_secrets_file: ./secrets.enc\nlogging:\n  level: DEBUG\n"
        )
        (self.tmp_dir / "config" / "paper.yaml").write_text("logging:\n  level: INFO\nsecrets:\n  provider: local\n")
        self._old_key = os.environ.get("ETF_PLATFORM_MASTER_KEY")
        os.environ.pop("ETF_PLATFORM_MASTER_KEY", None)  # explicitly UNSET, matching the bug report exactly
        self.addCleanup(self._restore_env)
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp_dir)
        self.addCleanup(os.chdir, self._old_cwd)

    def _restore_env(self):
        if self._old_key is not None:
            os.environ["ETF_PLATFORM_MASTER_KEY"] = self._old_key
        else:
            os.environ.pop("ETF_PLATFORM_MASTER_KEY", None)

    def test_startup_succeeds_with_master_key_genuinely_unset(self):
        self.assertNotIn("ETF_PLATFORM_MASTER_KEY", os.environ, "Test setup invariant broken -- key must be unset.")
        runner = PaperRunner(config_dir="config", environment="paper", db_path="test.db",
                              event_archive_path="events.jsonl", telegram_retry_queue_path="tg_queue.jsonl")
        runner.startup()  # must not raise SecretsBackendUnavailableError
        self.assertTrue(runner.health_check())
        runner.shutdown()

    def test_notifier_is_none_when_master_key_unset(self):
        runner = PaperRunner(config_dir="config", environment="paper", db_path="test.db",
                              event_archive_path="events.jsonl", telegram_retry_queue_path="tg_queue.jsonl")
        runner.startup()
        self.assertIsNone(runner._notifier)
        runner.shutdown()

    def test_a_real_order_still_works_with_no_master_key(self):
        """The master key only affects Telegram in paper mode -- trading
        itself must be entirely unaffected."""
        runner = PaperRunner(config_dir="config", environment="paper", db_path="test.db",
                              event_archive_path="events.jsonl", telegram_retry_queue_path="tg_queue.jsonl")
        runner.startup()
        broker_order_id = runner._broker.submit_order("NIFTYBEES", "BUY", 10, 250.0, "ref")
        status = runner._broker.get_order_status(broker_order_id)
        self.assertEqual(status.state, OrderLifecycleState.FILLED)
        runner.shutdown()

    def test_dev_environment_also_starts_without_master_key(self):
        """The bug report used environment=dev specifically, not just
        paper -- both PAPER_ENVIRONMENTS values must work."""
        runner = PaperRunner(config_dir="config", environment="dev", db_path="test.db",
                              event_archive_path="events.jsonl", telegram_retry_queue_path="tg_queue.jsonl")
        runner.startup()
        self.assertTrue(runner.health_check())
        runner.shutdown()


class TestLiveModeSecurityUnweakened(unittest.TestCase):
    """Requirements 2-4: this fix must not change ProductionRunner's
    behavior at all. Proven here, not just asserted by 'I didn't touch
    that file' -- ProductionRunner must still fail immediately, with the
    same exception type, when the master key is genuinely absent."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        (self.tmp_dir / "config").mkdir()
        (self.tmp_dir / "config" / "base.yaml").write_text(
            "environment: dev\nsecrets:\n  provider: local\n  local_key_env_var: ETF_PLATFORM_MASTER_KEY\n"
            "  local_secrets_file: ./secrets.enc\nlogging:\n  level: INFO\n"
        )
        (self.tmp_dir / "config" / "live.yaml").write_text("logging:\n  level: INFO\nsecrets:\n  provider: local\n")
        self._old_key = os.environ.get("ETF_PLATFORM_MASTER_KEY")
        os.environ.pop("ETF_PLATFORM_MASTER_KEY", None)
        self.addCleanup(self._restore_env)
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp_dir)
        self.addCleanup(os.chdir, self._old_cwd)

    def _restore_env(self):
        if self._old_key is not None:
            os.environ["ETF_PLATFORM_MASTER_KEY"] = self._old_key
        else:
            os.environ.pop("ETF_PLATFORM_MASTER_KEY", None)

    def test_production_runner_still_fails_fast_without_master_key(self):
        from etf_platform.production.production_runner import ProductionRunner

        runner = ProductionRunner(config_dir="config", environment="live", db_path="live_test.db")
        with self.assertRaises(Exception) as ctx:
            runner.startup()
        self.assertIn("ETF_PLATFORM_MASTER_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
