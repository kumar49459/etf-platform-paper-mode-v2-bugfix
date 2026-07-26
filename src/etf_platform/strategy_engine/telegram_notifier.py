"""TelegramNotificationPort (Milestone 7, Blocker 1). Implements
NotificationPort (frozen, exactly as designed -- send() and
poll_commands(), no interface changes) via the Telegram Bot API.

Per NotificationPort's own docstring ("a NotificationPort failure must
never block the underlying funding-check state machine - callers should
treat this as best-effort, not gate execution on its success"): every
method here catches everything, logs it, and returns without raising.
Trading logic must never crash because Telegram is unavailable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from etf_platform.common.logging_setup import get_logger
from etf_platform.common.retry import RetryExhaustedError, retry_with_backoff
from etf_platform.strategy_engine.ports import NotificationPort

logger = get_logger("strategy_engine.telegram_notifier")

TELEGRAM_API_BASE = "https://api.telegram.org"
_RECOGNIZED_COMMANDS = ("PAUSE", "RESUME", "DISCONTINUE")


class TelegramTransportError(Exception):
    """Wraps any failure from the injected HTTP transport -- network
    error, non-2xx response, malformed JSON. Never raised past this
    module's own boundary; caught internally and logged."""


@dataclass(frozen=True)
class QueuedNotification:
    message: str
    queued_at: str
    attempts: int


class NotificationRetryQueue:
    """Durable, append-only JSONL queue for messages that failed to send
    after exhausting retries. Same durability pattern as EventArchive/
    CycleLogArchive and TagMappingStore."""

    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(self, message):
        with open(self._path, "a") as f:
            f.write(json.dumps({"message": message, "queued_at": time.time(), "attempts": 0}) + "\n")

    def read_all(self):
        if not self._path.exists():
            return []
        rows = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def clear(self):
        if self._path.exists():
            self._path.unlink()

    def rewrite(self, remaining_rows):
        if not remaining_rows:
            self.clear()
            return
        with open(self._path, "w") as f:
            for row in remaining_rows:
                f.write(json.dumps(row) + "\n")


def _is_retryable(exc):
    return isinstance(exc, (TelegramTransportError, ConnectionError, TimeoutError))


class TelegramNotificationPort(NotificationPort):
    """transport is injectable and mockable -- must expose
    .request(method, url, data, timeout) -> (status_code, json_body),
    the same shape as KiteHTTPClient's transport."""

    def __init__(self, bot_token, chat_id, transport, retry_queue_path=None,
                 max_attempts=3, timeout_seconds=10.0, sleep_fn=None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._transport = transport
        self._max_attempts = max_attempts
        self._timeout = timeout_seconds
        self._sleep_fn = sleep_fn
        self._queue = NotificationRetryQueue(retry_queue_path) if retry_queue_path else None
        self._last_update_id = None

    def send(self, message):
        try:
            self._send_with_retry(message)
            logger.info("Telegram notification sent successfully.")
        except Exception as exc:
            logger.error("Telegram notification failed after retries: %s. Message: %r", exc, message)
            if self._queue is not None:
                self._queue.enqueue(message)
                logger.warning("Notification queued for later retry.")

    def _send_with_retry(self, message):
        def attempt():
            return self._raw_send(message)

        kwargs = {"is_retryable": _is_retryable, "max_attempts": self._max_attempts}
        if self._sleep_fn:
            kwargs["sleep_fn"] = self._sleep_fn
        try:
            retry_with_backoff(attempt, **kwargs)
        except RetryExhaustedError as exc:
            raise TelegramTransportError(f"Exhausted {self._max_attempts} attempts: {exc.last_exception}") from exc

    def _raw_send(self, message):
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        try:
            status_code, json_body = self._transport.request(
                method="POST", url=url, data={"chat_id": self._chat_id, "text": message}, timeout=self._timeout,
            )
        except Exception as exc:
            raise TelegramTransportError(f"Transport-level failure: {exc}") from exc
        if status_code >= 400:
            raise TelegramTransportError(f"Telegram API returned {status_code}: {json_body}")
        if not (json_body or {}).get("ok", False):
            raise TelegramTransportError(f"Telegram API reported failure: {json_body}")
        return json_body

    def retry_queued(self):
        if self._queue is None:
            return 0, 0
        rows = self._queue.read_all()
        if not rows:
            return 0, 0
        still_failed = []
        sent_count = 0
        for row in rows:
            try:
                self._send_with_retry(row["message"])
                sent_count += 1
            except Exception:
                row["attempts"] = row.get("attempts", 0) + 1
                still_failed.append(row)
        self._queue.rewrite(still_failed)
        return sent_count, len(still_failed)

    def poll_commands(self):
        try:
            return self._raw_poll_commands()
        except Exception as exc:
            logger.error("Telegram poll_commands failed: %s. Returning no commands this cycle.", exc)
            return []

    def _raw_poll_commands(self):
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/getUpdates"
        data = {"offset": self._last_update_id + 1} if self._last_update_id is not None else {}
        status_code, json_body = self._transport.request(method="GET", url=url, data=data, timeout=self._timeout)
        if status_code >= 400 or not (json_body or {}).get("ok", False):
            raise TelegramTransportError(f"getUpdates failed: {status_code} {json_body}")

        commands = []
        for update in json_body.get("result", []):
            self._last_update_id = update["update_id"]
            text = (update.get("message", {}) or {}).get("text", "").strip().upper()
            if text in _RECOGNIZED_COMMANDS:
                commands.append(text)
            elif text:
                logger.debug("Ignoring unrecognized Telegram command: %r", text)
        return commands
