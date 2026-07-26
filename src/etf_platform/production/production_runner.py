"""Production Runner (Milestone 7, Blocker 2). Pure orchestration --
wires already-frozen, already-tested components together into a runnable
process. Introduces zero new business logic; every trading-relevant
decision still lives exactly where it already did (SubmissionOrchestrator,
ReconciliationService, VerificationService, DDR-001's AMBIGUOUS policy).
This class's only job is startup, health checking, and graceful shutdown.
"""

from __future__ import annotations

import signal

from etf_platform.common.logging_setup import configure_logging, get_logger
from etf_platform.common.requests_http_transport import RequestsHTTPTransport
from etf_platform.config_manager.config_manager import ConfigManager
from etf_platform.execution_manager import (
    ExecutionStateStore,
    InMemoryEventRecorder,
    MinimalInlineComplianceChecker,
    ReconciliationService,
    SubmissionOrchestrator,
    SystemClock,
)
from etf_platform.execution_manager.kite_broker import KiteBrokerPort
from etf_platform.execution_manager.kite_credentials import MissingKiteCredentialsError, load_kite_auth_manager
from etf_platform.execution_manager.kite_http_client import KiteHTTPClient
from etf_platform.execution_manager.kite_tag_encoding import TagMappingStore
from etf_platform.paper_trading_operations.event_archive import EventArchive
from etf_platform.secrets_manager.exceptions import SecretNotFoundError
from etf_platform.secrets_manager.secrets_manager import SecretsManager
from etf_platform.strategy_engine.telegram_notifier import TelegramNotificationPort

logger = get_logger("execution_manager.production_runner")

TELEGRAM_BOT_TOKEN_SECRET_NAME = "telegram_bot_token"
TELEGRAM_CHAT_ID_SECRET_NAME = "telegram_chat_id"
KITE_ACCESS_TOKEN_SECRET_NAME = "kite_access_token"
"""The daily-rotating access_token, obtained via the mandatory manual
login flow (Decision 3 -- never automated by this platform), is itself
treated as a secret: an operator's manual login step is expected to
store it via SecretsManager.set_secret() once per trading day, and this
runner reads it at startup rather than attempting to produce one itself."""


class StartupValidationError(Exception):
    """Raised when any startup validation check fails -- fail fast, with
    a message clear enough that an operator doesn't need to read this
    file to understand what's wrong."""


class ProductionRunner:
    def __init__(self, config_dir="config", environment=None, db_path=None,
                 tag_mapping_path=None, telegram_retry_queue_path=None, event_archive_path=None,
                 http_transport=None, telegram_transport=None, clock=None):
        self._config_dir = config_dir
        self._environment = environment
        self._db_path = db_path or "data/execution_state.db"
        self._tag_mapping_path = tag_mapping_path or "data/kite_tag_mapping.jsonl"
        self._telegram_retry_queue_path = telegram_retry_queue_path or "data/telegram_retry_queue.jsonl"
        self._event_archive_path = event_archive_path or "data/production_events.jsonl"
        self._http_transport = http_transport
        self._telegram_transport = telegram_transport
        """Milestone 8: previously defaulted straight to None with no
        fallback -- reproduced directly as a real defect (StartupValidationError
        on the very first live API call, since None has no .request()
        method). Existing dependency injection is unchanged: an explicitly
        supplied transport (e.g. a test's mock) is used exactly as before.
        Only the previously-missing default case is now handled, in
        startup() below, not here -- constructed lazily so a caller that
        never calls startup() (e.g. every existing test that only
        constructs ProductionRunner to check __init__ behavior) never
        pays for or depends on a real requests.Session existing."""
        self._clock = clock or SystemClock()
        self._notifier = None
        self._store = None
        self._broker = None
        self._orchestrator = None
        self._reconciler = None
        self._auth = None
        self._config = None
        self._events = None
        self._event_archive = None
        self._shutdown_requested = False

    def startup(self):
        if self._http_transport is None:
            self._http_transport = RequestsHTTPTransport()
        if self._telegram_transport is None:
            self._telegram_transport = RequestsHTTPTransport()
        self._config = ConfigManager(config_dir=self._config_dir, environment=self._environment).load()
        configure_logging(
            level=getattr(self._config.logging, "level", "INFO"),
            log_dir=getattr(self._config.logging, "log_dir", None),
        )
        logger.info("Configuration loaded. Environment=%s, config_version=%s",
                    self._config.environment, self._config.config_version)

        secrets_manager = SecretsManager(self._config.secrets)

        self._notifier = self._build_notifier(secrets_manager)

        try:
            self._auth = load_kite_auth_manager(secrets_manager)
        except MissingKiteCredentialsError as exc:
            self._notify_best_effort(f"[STARTUP FAILED] Missing Kite credentials: {exc}")
            raise StartupValidationError(str(exc)) from exc

        try:
            access_token = secrets_manager.get_secret(KITE_ACCESS_TOKEN_SECRET_NAME)
        except SecretNotFoundError as exc:
            message = (
                f"Cannot start: no '{KITE_ACCESS_TOKEN_SECRET_NAME}' secret found. Complete the manual daily "
                f"login flow and store the resulting access_token via SecretsManager before starting -- "
                f"this platform never attempts to generate a session on its own (Decision 3)."
            )
            self._notify_best_effort(f"[STARTUP FAILED] {message}")
            raise StartupValidationError(message) from exc
        self._auth.set_session(access_token=access_token, acquired_at=self._clock.now())

        http_client = KiteHTTPClient(
            api_key=self._auth.api_key, access_token=self._auth.get_access_token(),
            transport=self._http_transport,
        )
        tags = TagMappingStore(self._tag_mapping_path)
        self._broker = KiteBrokerPort(http_client, tags)

        try:
            available_cash = self._broker.get_available_cash()
        except Exception as exc:
            self._notify_best_effort(f"[STARTUP FAILED] Could not reach Kite API (get_available_cash): {exc}")
            raise StartupValidationError(f"Startup health check failed: could not reach Kite API: {exc}") from exc
        logger.info("Startup health check passed. Available cash: %s", available_cash)

        compliance_checker = MinimalInlineComplianceChecker(static_ip_verified=False)
        """Deliberately defaults to False here, unlike the class's own
        constructor default of True -- found during the Live Readiness
        Review as a fail-open risk (Risk Assessment, Risk 6). The
        production runner requires the operator to explicitly confirm
        static-IP compliance is real before this flips to True."""

        self._store = ExecutionStateStore(self._db_path)
        self._events = InMemoryEventRecorder()
        self._event_archive = EventArchive(self._event_archive_path)
        self._orchestrator = SubmissionOrchestrator(
            self._store, self._broker, self._broker, compliance_checker, self._notifier, self._clock, self._events,
        )
        self._reconciler = ReconciliationService(self._store, self._broker, self._clock, self._events, self._notifier)

        outcomes = self._reconciler.reconcile(correlation_id="production-startup")
        logger.info("Startup reconciliation complete: %d record(s) checked.", len(outcomes))

        self._register_signal_handlers()
        self._notify_best_effort(
            f"[STARTUP] Production runner started. Environment={self._config.environment}. "
            f"Available cash: {available_cash}. Reconciliation checked {len(outcomes)} record(s)."
        )
        logger.info("Startup complete.")

    def _build_notifier(self, secrets_manager):
        try:
            bot_token = secrets_manager.get_secret(TELEGRAM_BOT_TOKEN_SECRET_NAME)
            chat_id = secrets_manager.get_secret(TELEGRAM_CHAT_ID_SECRET_NAME)
        except SecretNotFoundError as exc:
            raise StartupValidationError(
                f"Cannot start: Telegram credentials ('{TELEGRAM_BOT_TOKEN_SECRET_NAME}', "
                f"'{TELEGRAM_CHAT_ID_SECRET_NAME}') not found in SecretsManager. Running without a working "
                f"notification channel is exactly the gap the Live Readiness Review identified as this "
                f"platform's top blocker -- refusing to start without it."
            ) from exc
        return TelegramNotificationPort(
            bot_token=bot_token, chat_id=chat_id, transport=self._telegram_transport,
            retry_queue_path=self._telegram_retry_queue_path,
        )

    def _notify_best_effort(self, message):
        if self._notifier is not None:
            self._notifier.send(message)
        else:
            logger.error("Cannot send notification (no notifier available yet): %s", message)

    def health_check(self):
        try:
            self._broker.get_available_cash()
            return True
        except Exception as exc:
            logger.error("Health check failed: %s", exc)
            return False

    def _register_signal_handlers(self):
        def _handle(signum, frame):
            logger.info("Received signal %s, requesting graceful shutdown.", signum)
            self._shutdown_requested = True

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)

    def shutdown_requested(self):
        return self._shutdown_requested

    def shutdown(self):
        logger.info("Shutting down.")
        self._notify_best_effort("[SHUTDOWN] Production runner shutting down.")
        if self._event_archive is not None and self._events is not None:
            archived_count = self._event_archive.archive_and_clear(self._events)
            logger.info("Archived %d event(s) before shutdown.", archived_count)
        if self._store is not None:
            self._store.close()
        logger.info("Shutdown complete.")
