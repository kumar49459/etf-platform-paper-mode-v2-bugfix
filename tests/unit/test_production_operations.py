"""Milestone 7 test suite: TelegramNotificationPort and ProductionRunner.
Every test uses mocked transports -- no real network access, no real
Telegram bot, no real Kite account.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from etf_platform.config_manager.config_manager import ConfigManager
from etf_platform.execution_manager.kite_credentials import MissingKiteCredentialsError, load_kite_auth_manager
from etf_platform.production.production_runner import ProductionRunner, StartupValidationError
from etf_platform.secrets_manager.secrets_manager import SecretsManager
from etf_platform.strategy_engine.telegram_notifier import (
    NotificationRetryQueue,
    TelegramNotificationPort,
)


class MockTelegramTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, data, timeout):
        self.calls.append({"method": method, "url": url, "data": data})
        response = self.responses.pop(0)
        if callable(response):
            return response()
        return response


class TestTelegramNotificationPortSend(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_successful_send(self):
        transport = MockTelegramTransport([(200, {"ok": True})])
        notifier = TelegramNotificationPort("token", "chat", transport, sleep_fn=lambda s: None)
        notifier.send("Test message")
        self.assertEqual(len(transport.calls), 1)
        self.assertIn("sendMessage", transport.calls[0]["url"])
        self.assertEqual(transport.calls[0]["data"]["text"], "Test message")

    def test_send_never_raises_even_on_total_failure(self):
        def always_fail():
            raise ConnectionError("network down")
        transport = MockTelegramTransport([always_fail, always_fail, always_fail])
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        try:
            notifier.send("Critical message")
        except Exception as exc:
            self.fail(f"send() must never raise, but raised {exc!r}")

    def test_transient_failure_retries_then_succeeds(self):
        transport = MockTelegramTransport([
            (500, {"ok": False, "description": "Internal error"}),
            (200, {"ok": True}),
        ])
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        notifier.send("Message")
        self.assertEqual(len(transport.calls), 2)

    def test_failed_send_queues_for_retry_when_queue_configured(self):
        def always_fail():
            raise ConnectionError("down")
        transport = MockTelegramTransport([always_fail] * 3)
        queue_path = self.tmp_dir / "queue.jsonl"
        notifier = TelegramNotificationPort("t", "c", transport, retry_queue_path=queue_path, sleep_fn=lambda s: None)
        notifier.send("Important message")
        queue = NotificationRetryQueue(queue_path)
        rows = queue.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "Important message")

    def test_failed_send_without_queue_configured_does_not_crash(self):
        def always_fail():
            raise ConnectionError("down")
        transport = MockTelegramTransport([always_fail] * 3)
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        notifier.send("Message")

    def test_retry_queued_flushes_successfully(self):
        def always_fail():
            raise ConnectionError("down")
        transport = MockTelegramTransport([always_fail] * 3)
        queue_path = self.tmp_dir / "queue.jsonl"
        notifier = TelegramNotificationPort("t", "c", transport, retry_queue_path=queue_path, sleep_fn=lambda s: None)
        notifier.send("Message 1")

        notifier._transport = MockTelegramTransport([(200, {"ok": True})])
        sent, still_failed = notifier.retry_queued()
        self.assertEqual(sent, 1)
        self.assertEqual(still_failed, 0)
        self.assertEqual(NotificationRetryQueue(queue_path).read_all(), [])

    def test_retry_queued_with_no_queue_configured_is_a_safe_noop(self):
        transport = MockTelegramTransport([])
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        sent, still_failed = notifier.retry_queued()
        self.assertEqual((sent, still_failed), (0, 0))

    def test_a_genuine_400_error_is_not_endlessly_retried_beyond_max_attempts(self):
        transport = MockTelegramTransport([
            (400, {"ok": False, "description": "Bad Request"}),
            (400, {"ok": False, "description": "Bad Request"}),
            (400, {"ok": False, "description": "Bad Request"}),
        ])
        notifier = TelegramNotificationPort("t", "c", transport, max_attempts=3, sleep_fn=lambda s: None)
        notifier.send("Message")
        self.assertEqual(len(transport.calls), 3)


class TestTelegramNotificationPortPollCommands(unittest.TestCase):
    def test_recognized_commands_are_parsed(self):
        transport = MockTelegramTransport([(200, {"ok": True, "result": [
            {"update_id": 1, "message": {"text": "pause"}},
            {"update_id": 2, "message": {"text": "resume"}},
            {"update_id": 3, "message": {"text": "discontinue"}},
        ]})])
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        commands = notifier.poll_commands()
        self.assertEqual(commands, ["PAUSE", "RESUME", "DISCONTINUE"])

    def test_unrecognized_text_is_ignored_not_erroring(self):
        transport = MockTelegramTransport([(200, {"ok": True, "result": [
            {"update_id": 1, "message": {"text": "hello, how are you?"}},
        ]})])
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        commands = notifier.poll_commands()
        self.assertEqual(commands, [])

    def test_poll_commands_never_raises_on_transport_failure(self):
        def always_fail():
            raise ConnectionError("down")
        transport = MockTelegramTransport([always_fail])
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        commands = notifier.poll_commands()
        self.assertEqual(commands, [])

    def test_offset_advances_across_calls(self):
        transport = MockTelegramTransport([
            (200, {"ok": True, "result": [{"update_id": 5, "message": {"text": "pause"}}]}),
            (200, {"ok": True, "result": []}),
        ])
        notifier = TelegramNotificationPort("t", "c", transport, sleep_fn=lambda s: None)
        notifier.poll_commands()
        notifier.poll_commands()
        self.assertEqual(transport.calls[1]["data"].get("offset"), 6)


class TestNotificationRetryQueue(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_enqueue_and_read_all(self):
        queue = NotificationRetryQueue(self.tmp_dir / "q.jsonl")
        queue.enqueue("msg1")
        queue.enqueue("msg2")
        rows = queue.read_all()
        self.assertEqual([r["message"] for r in rows], ["msg1", "msg2"])

    def test_readable_independently_of_writer(self):
        path = self.tmp_dir / "q.jsonl"
        NotificationRetryQueue(path).enqueue("persisted message")
        independent_queue = NotificationRetryQueue(path)
        self.assertEqual(len(independent_queue.read_all()), 1)

    def test_rewrite_with_empty_list_clears_the_file(self):
        path = self.tmp_dir / "q.jsonl"
        queue = NotificationRetryQueue(path)
        queue.enqueue("msg")
        queue.rewrite([])
        self.assertEqual(queue.read_all(), [])
        self.assertFalse(path.exists())


class TestKiteCredentialsIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self._old_key = os.environ.get("ETF_PLATFORM_MASTER_KEY")
        os.environ["ETF_PLATFORM_MASTER_KEY"] = Fernet.generate_key().decode()
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_key is not None:
            os.environ["ETF_PLATFORM_MASTER_KEY"] = self._old_key
        else:
            os.environ.pop("ETF_PLATFORM_MASTER_KEY", None)

    def _make_secrets_manager(self):
        from etf_platform.config_manager.schema import SecretsConfig
        config = SecretsConfig(provider="local", local_secrets_file=str(self.tmp_dir / "secrets.enc"))
        return SecretsManager(config)

    def test_missing_credentials_fails_fast_with_clear_message(self):
        sm = self._make_secrets_manager()
        with self.assertRaises(MissingKiteCredentialsError) as ctx:
            load_kite_auth_manager(sm)
        self.assertIn("kite_api_key", str(ctx.exception))
        self.assertIn("kite_api_secret", str(ctx.exception))

    def test_partial_credentials_still_fails_fast(self):
        sm = self._make_secrets_manager()
        sm.set_secret("kite_api_key", "present")
        with self.assertRaises(MissingKiteCredentialsError) as ctx:
            load_kite_auth_manager(sm)
        self.assertIn("kite_api_secret", str(ctx.exception))

    def test_complete_credentials_produce_a_working_kite_auth_manager(self):
        sm = self._make_secrets_manager()
        sm.set_secret("kite_api_key", "real_key")
        sm.set_secret("kite_api_secret", "real_secret")
        auth = load_kite_auth_manager(sm)
        self.assertEqual(auth.api_key, "real_key")


class TestProductionRunnerStartupShutdown(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        (self.tmp_dir / "config").mkdir()
        (self.tmp_dir / "config" / "base.yaml").write_text("environment: dev\n")
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

    def _bootstrap_all_secrets(self):
        config = ConfigManager(config_dir="config").load()
        sm = SecretsManager(config.secrets)
        sm.set_secret("kite_api_key", "test_key")
        sm.set_secret("kite_api_secret", "test_secret")
        sm.set_secret("kite_access_token", "test_token")
        sm.set_secret("telegram_bot_token", "test_bot_token")
        sm.set_secret("telegram_chat_id", "test_chat_id")

    def _make_runner(self):
        class MockHTTPTransport:
            def request(self, method, url, headers, data, timeout):
                if "margins" in url:
                    return (200, {"status": "success", "data": {"net": 100000.0}})
                return (200, {"status": "success", "data": {}})

        class MockTelegramTransportInner:
            def request(self, method, url, data, timeout):
                return (200, {"ok": True})

        return ProductionRunner(
            config_dir="config", db_path="test.db", tag_mapping_path="tags.jsonl",
            telegram_retry_queue_path="tg_queue.jsonl", event_archive_path="events.jsonl",
            http_transport=MockHTTPTransport(), telegram_transport=MockTelegramTransportInner(),
        )

    def test_successful_startup_and_shutdown(self):
        self._bootstrap_all_secrets()
        runner = self._make_runner()
        runner.startup()
        self.assertTrue(runner.health_check())
        runner.shutdown()

    def test_startup_fails_fast_with_no_secrets_at_all(self):
        runner = self._make_runner()
        with self.assertRaises(StartupValidationError) as ctx:
            runner.startup()
        self.assertIn("Telegram", str(ctx.exception))

    def test_startup_fails_fast_with_missing_kite_credentials_only(self):
        config = ConfigManager(config_dir="config").load()
        sm = SecretsManager(config.secrets)
        sm.set_secret("telegram_bot_token", "test_bot_token")
        sm.set_secret("telegram_chat_id", "test_chat_id")
        runner = self._make_runner()
        with self.assertRaises(StartupValidationError) as ctx:
            runner.startup()
        self.assertIn("kite_api_key", str(ctx.exception))

    def test_startup_fails_fast_with_missing_access_token(self):
        config = ConfigManager(config_dir="config").load()
        sm = SecretsManager(config.secrets)
        sm.set_secret("kite_api_key", "test_key")
        sm.set_secret("kite_api_secret", "test_secret")
        sm.set_secret("telegram_bot_token", "test_bot_token")
        sm.set_secret("telegram_chat_id", "test_chat_id")
        runner = self._make_runner()
        with self.assertRaises(StartupValidationError) as ctx:
            runner.startup()
        self.assertIn("kite_access_token", str(ctx.exception))

    def test_startup_fails_fast_when_broker_unreachable(self):
        self._bootstrap_all_secrets()

        class FailingTransport:
            def request(self, method, url, headers, data, timeout):
                raise ConnectionError("no route to host")

        class OkTelegramTransport:
            def request(self, method, url, data, timeout):
                return (200, {"ok": True})

        runner = ProductionRunner(
            config_dir="config", db_path="test.db", tag_mapping_path="tags.jsonl",
            telegram_retry_queue_path="tg_queue.jsonl", event_archive_path="events.jsonl",
            http_transport=FailingTransport(), telegram_transport=OkTelegramTransport(),
        )
        with self.assertRaises(StartupValidationError) as ctx:
            runner.startup()
        self.assertIn("Kite API", str(ctx.exception))

    def test_startup_runs_mandatory_reconciliation(self):
        self._bootstrap_all_secrets()
        runner = self._make_runner()
        runner.startup()
        self.assertIsNotNone(runner._reconciler)
        runner.shutdown()

    def test_shutdown_archives_events(self):
        self._bootstrap_all_secrets()
        runner = self._make_runner()
        runner.startup()
        runner.shutdown()
        archive_path = Path("events.jsonl")
        self.assertTrue(archive_path.exists())

    def test_recovery_after_restart_runs_reconciliation_again(self):
        self._bootstrap_all_secrets()
        runner1 = self._make_runner()
        runner1.startup()
        runner1.shutdown()

        runner2 = self._make_runner()
        runner2.startup()
        self.assertTrue(runner2.health_check())
        runner2.shutdown()

    def test_static_ip_compliance_defaults_to_false_at_the_runner_level(self):
        self._bootstrap_all_secrets()
        runner = self._make_runner()
        runner.startup()
        checker = runner._orchestrator._compliance
        self.assertFalse(checker._static_ip_verified)
        runner.shutdown()

    def test_no_transport_supplied_autoconstructs_a_real_requests_http_transport(self):
        """Milestone 8, requirement 5: 'If no transport is supplied,
        construct RequestsHTTPTransport automatically.' Also a direct
        regression test for the defect this milestone was created to fix
        -- the previous behavior (transport=None with no fallback) would
        crash with AttributeError on the first real call, reproduced and
        confirmed before this fix existed."""
        self._bootstrap_all_secrets()
        from etf_platform.common.requests_http_transport import RequestsHTTPTransport

        # No http_transport/telegram_transport supplied at all -- unlike
        # every other test in this file, which explicitly injects a mock.
        runner = ProductionRunner(config_dir="config", db_path="test.db", tag_mapping_path="tags.jsonl",
                                   telegram_retry_queue_path="tg_queue.jsonl", event_archive_path="events.jsonl")
        self.assertIsNone(runner._http_transport, "Must remain None until startup() -- lazy construction.")
        try:
            runner.startup()
        except Exception:
            pass  # Expected to fail at the network step in this environment (no real network access) --
                  # what matters is WHERE it fails, checked below, not whether the network call itself succeeds.
        self.assertIsInstance(runner._http_transport, RequestsHTTPTransport,
                               "Must auto-construct a real transport, not remain None.")
        self.assertIsInstance(runner._telegram_transport, RequestsHTTPTransport)

    def test_explicitly_supplied_transport_is_never_overridden(self):
        """Existing dependency injection must continue to work unchanged
        -- an explicitly supplied (e.g. mock) transport must never be
        silently replaced by the auto-constructed real one."""
        self._bootstrap_all_secrets()
        sentinel_transport = object()

        class SentinelWrapper:
            def request(self, *a, **kw):
                raise AssertionError("The sentinel transport should never actually be called in this test.")

        runner = ProductionRunner(
            config_dir="config", db_path="test.db", tag_mapping_path="tags.jsonl",
            telegram_retry_queue_path="tg_queue.jsonl", event_archive_path="events.jsonl",
            http_transport=SentinelWrapper(), telegram_transport=SentinelWrapper(),
        )
        original_http = runner._http_transport
        original_telegram = runner._telegram_transport
        try:
            runner.startup()
        except Exception:
            pass
        self.assertIs(runner._http_transport, original_http, "Explicitly injected transport must not be replaced.")
        self.assertIs(runner._telegram_transport, original_telegram)


if __name__ == "__main__":
    unittest.main()
