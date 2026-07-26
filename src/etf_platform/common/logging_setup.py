"""Structured logging setup shared across all Phase 2 modules.

Design decisions (see PHASE1_Architecture_SRS.md §1.4, §13.2 for context):
- A single process-wide `SecretScrubbingFilter` is attached to the root logger so that
  even if a bug elsewhere accidentally logs a secret value, it never reaches disk in
  plaintext. This is defense-in-depth on top of the Secrets Manager's own discipline
  of never returning secrets into code paths that log them.
- Plain `logging` (stdlib) rather than structlog/loguru: keeps the live-instance
  dependency footprint minimal (binding constraint from §12.1). A JSON formatter is
  provided for environments (e.g. CloudWatch) that benefit from structured logs, but
  plain text is the default for local development readability.
- Logger names follow `etf_platform.<module>` so log filtering/routing per module is
  trivial in CloudWatch or any log aggregator later.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Iterable


class SecretScrubbingFilter(logging.Filter):
    """Redacts any registered secret value that appears in a log record.

    Secrets register themselves (their *values*, not their names) via
    `register_secret_value()` the moment they are retrieved from the Secrets
    Manager. This filter then substring-replaces those values with a fixed
    redaction marker in every subsequent log message, args, and formatted
    exception text — regardless of which logger emitted the record.
    """

    _REDACTED = "***REDACTED***"

    def __init__(self) -> None:
        super().__init__(name="secret_scrubbing")
        self._secret_values: set[str] = set()

    def register_secret_value(self, value: str) -> None:
        if value:
            # Ignore trivially short values (e.g. "1") to avoid over-redacting
            # ordinary log content by accident.
            if len(value) >= 6:
                self._secret_values.add(value)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secret_values:
            return True
        msg = record.getMessage()
        scrubbed = msg
        for secret in self._secret_values:
            if secret in scrubbed:
                scrubbed = scrubbed.replace(secret, self._REDACTED)
        if scrubbed != msg:
            record.msg = scrubbed
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Minimal JSON line formatter, suitable for CloudWatch Logs ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_SCRUBBER = SecretScrubbingFilter()


def get_secret_scrubbing_filter() -> SecretScrubbingFilter:
    """Return the single process-wide scrubbing filter instance.

    The Secrets Manager calls `register_secret_value()` on this exact instance
    whenever it hands out a secret, so logging and secrets management stay
    decoupled (no import cycle) while still sharing state.
    """
    return _SCRUBBER


def configure_logging(
    *,
    level: str = "INFO",
    log_dir: str | Path | None = None,
    json_format: bool = False,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Configure the root logger once, at process startup.

    Idempotent: safe to call multiple times (e.g. once in production code, once
    again in a test's setUp) — it clears and re-installs handlers rather than
    stacking duplicates.
    """
    root = logging.getLogger("etf_platform")
    root.setLevel(level.upper())
    for old_handler in root.handlers:
        old_handler.close()
    root.handlers.clear()
    root.propagate = False

    formatter: logging.Formatter
    if json_format:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_SCRUBBER)
    root.addHandler(console_handler)

    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "etf_platform.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_SCRUBBER)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger, e.g. get_logger('data_engine.nse_provider')."""
    return logging.getLogger(f"etf_platform.{name}")
