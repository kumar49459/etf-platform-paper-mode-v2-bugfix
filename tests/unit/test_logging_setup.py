"""Unit tests for common.logging_setup, especially the secret-scrubbing
filter — this is a security-relevant module, so it gets direct test
coverage rather than being verified only incidentally via secrets_manager
tests."""

from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.common.logging_setup import (
    JsonFormatter,
    SecretScrubbingFilter,
    configure_logging,
    get_logger,
    get_secret_scrubbing_filter,
)


class TestSecretScrubbingFilter(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = SecretScrubbingFilter()

    def _make_record(self, message: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg=message, args=(), exc_info=None,
        )

    def test_registered_secret_is_redacted(self) -> None:
        self.filter.register_secret_value("super-secret-token-value")
        record = self._make_record("Request failed with token super-secret-token-value in header")
        self.filter.filter(record)
        self.assertNotIn("super-secret-token-value", record.getMessage())
        self.assertIn("REDACTED", record.getMessage())

    def test_unregistered_value_not_touched(self) -> None:
        record = self._make_record("This is a totally normal log message")
        original = record.getMessage()
        self.filter.filter(record)
        self.assertEqual(record.getMessage(), original)

    def test_short_values_ignored_to_avoid_over_redaction(self) -> None:
        # e.g. a secret value of "1" or "ok" should not cause every
        # occurrence of "1" in unrelated log messages to be redacted.
        self.filter.register_secret_value("ok")
        record = self._make_record("Status: ok, processed 5 rows")
        self.filter.filter(record)
        self.assertEqual(record.getMessage(), "Status: ok, processed 5 rows")

    def test_multiple_secrets_all_redacted(self) -> None:
        self.filter.register_secret_value("first-secret-value")
        self.filter.register_secret_value("second-secret-value")
        record = self._make_record("Keys: first-secret-value and second-secret-value")
        self.filter.filter(record)
        msg = record.getMessage()
        self.assertNotIn("first-secret-value", msg)
        self.assertNotIn("second-secret-value", msg)

    def test_filter_returns_true_always(self) -> None:
        # A logging Filter returning False would suppress the record
        # entirely — that's not this filter's job, it only redacts.
        record = self._make_record("anything")
        self.assertTrue(self.filter.filter(record))

    def test_process_wide_singleton_shared(self) -> None:
        f1 = get_secret_scrubbing_filter()
        f2 = get_secret_scrubbing_filter()
        self.assertIs(f1, f2)


class TestJsonFormatter(unittest.TestCase):
    def test_formats_valid_json_with_expected_fields(self) -> None:
        import json

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="etf_platform.test", level=logging.WARNING, pathname=__file__, lineno=10,
            msg="something happened", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["level"], "WARNING")
        self.assertEqual(parsed["logger"], "etf_platform.test")
        self.assertEqual(parsed["message"], "something happened")


class TestConfigureLogging(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_configure_logging_is_idempotent(self) -> None:
        configure_logging(level="DEBUG", log_dir=None)
        configure_logging(level="DEBUG", log_dir=None)
        root = logging.getLogger("etf_platform")
        # Calling twice must not stack duplicate handlers.
        self.assertEqual(len(root.handlers), 1)

    def test_log_file_created_when_log_dir_given(self) -> None:
        configure_logging(level="INFO", log_dir=self.tmp_dir)
        logger = get_logger("test_module")
        logger.info("hello world")
        log_file = self.tmp_dir / "etf_platform.log"
        self.assertTrue(log_file.exists())
        self.assertIn("hello world", log_file.read_text())

    def test_get_logger_uses_namespaced_prefix(self) -> None:
        logger = get_logger("data_engine.foo")
        self.assertEqual(logger.name, "etf_platform.data_engine.foo")

    def test_secrets_redacted_in_actual_file_output(self) -> None:
        configure_logging(level="INFO", log_dir=self.tmp_dir)
        scrubber = get_secret_scrubbing_filter()
        scrubber.register_secret_value("integration-test-secret-xyz")
        logger = get_logger("test_module")
        logger.info("Using credential integration-test-secret-xyz for auth")
        log_file = self.tmp_dir / "etf_platform.log"
        content = log_file.read_text()
        self.assertNotIn("integration-test-secret-xyz", content)
        self.assertIn("REDACTED", content)


if __name__ == "__main__":
    unittest.main()
