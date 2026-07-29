"""PaperRunner (authorized as new development work, outside the prior
freeze, specifically for local/dev Paper Trading Mode -- explicitly NOT
a production hotfix). Mirrors ProductionRunner's shape and startup
sequence exactly where the two genuinely share behavior (config loading,
logging, SecretsManager, Telegram notifications, mandatory reconciliation,
signal handling, graceful shutdown) and diverges only where paper mode
must: no KiteAuthManager, no Kite credentials of any kind, no
KiteBrokerPort/RequestsHTTPTransport. Uses PaperBrokerPort +
PaperQuoteProvider (Module 28's existing, frozen, 100,000-cycle-tested
simulator) instead.

Deliberately built as its OWN class rather than a flag inside
ProductionRunner -- keeps ProductionRunner (frozen) completely untouched.
"""

from __future__ import annotations

import signal

from etf_platform.common.logging_setup import configure_logging, get_logger
from etf_platform.config_manager.config_manager import ConfigManager
from etf_platform.execution_manager import (
    ExecutionStateStore,
    InMemoryEventRecorder,
    MinimalInlineComplianceChecker,
    PaperBrokerPort,
    PaperQuoteProvider,
    ReconciliationService,
    SubmissionOrchestrator,
    SystemClock,
)
from etf_platform.execution_manager.scenarios import BrokerScenario, FixedScenarioProvider
from etf_platform.paper_trading_operations.event_archive import EventArchive
from etf_platform.secrets_manager.exceptions import SecretsProviderError
from etf_platform.secrets_manager.secrets_manager import SecretsManager
from etf_platform.strategy_engine.telegram_notifier import TelegramNotificationPort

logger = get_logger("paper_trading_operations.paper_runner")

TELEGRAM_BOT_TOKEN_SECRET_NAME = "telegram_bot_token"
TELEGRAM_CHAT_ID_SECRET_NAME = "telegram_chat_id"

DEFAULT_BASE_PRICES = {
    # Illustrative starting prices for local paper-mode development only --
    # NOT sourced from any live quote, NOT verified against real market
    # data. Overridable via the base_prices constructor argument.
    "NIFTYBEES": 250.0,
    "GOLDBEES": 60.0,
    "BANKBEES": 500.0,
    "JUNIORBEES": 700.0,
    "LIQUIDBEES": 1000.0,
}


class PaperStartupValidationError(Exception):
    """Mirrors ProductionRunner's StartupValidationError -- a distinct
    class, so a caller can tell from the exception type alone which
    runner failed."""


class PaperRunner:
    def __init__(self, config_dir="config", environment="paper", db_path=None,
                 event_archive_path=None, telegram_retry_queue_path=None,
                 telegram_transport=None, base_prices=None, scenario_provider=None,
                 starting_cash=10_000_000.0, clock=None):
        self._config_dir = config_dir
        self._environment = environment
        self._db_path = db_path or "data/paper_execution_state.db"
        self._event_archive_path = event_archive_path or "data/paper_events.jsonl"
        self._telegram_retry_queue_path = telegram_retry_queue_path or "data/paper_telegram_retry_queue.jsonl"
        self._telegram_transport = telegram_transport
        self._base_prices = dict(base_prices) if base_prices else dict(DEFAULT_BASE_PRICES)
        self._starting_cash = starting_cash
        self._clock = clock or SystemClock()
        # Default: every order fills immediately at the quoted price --
        # deliberately NOT randomized failure injection. This class is
        # for a human developer to interact with a working system
        # predictably, not to stress-test it.
        self._scenario_provider = scenario_provider or FixedScenarioProvider(BrokerScenario.IMMEDIATE_FILL)
        self._notifier = None
        self._store = None
        self._broker = None
        self._quotes = None
        self._orchestrator = None
        self._reconciler = None
        self._config = None
        self._events = None
        self._event_archive = None
        self._shutdown_requested = False

    def startup(self):
        self._config = ConfigManager(config_dir=self._config_dir, environment=self._environment).load()
        configure_logging(
            level=getattr(self._config.logging, "level", "INFO"),
            log_dir=getattr(self._config.logging, "log_dir", None),
        )
        logger.info("PaperRunner starting. Environment=%s, config_version=%s -- NO Kite credentials required.",
                    self._config.environment, self._config.config_version)

        # SecretsManager is deliberately NOT constructed here anymore --
        # see _build_notifier()'s docstring for why. Paper mode's only
        # use of secrets is Telegram, which is optional; nothing in
        # startup() should require ETF_PLATFORM_MASTER_KEY to be set.
        self._notifier = self._build_notifier(self._config.secrets)

        self._events = InMemoryEventRecorder()
        self._event_archive = EventArchive(self._event_archive_path)

        self._broker = PaperBrokerPort(
            self._clock, self._events, self._scenario_provider, starting_cash=self._starting_cash,
        )
        self._quotes = PaperQuoteProvider(self._clock, self._events, self._scenario_provider, self._base_prices)

        compliance_checker = MinimalInlineComplianceChecker(static_ip_verified=False)

        self._store = ExecutionStateStore(self._db_path)
        self._orchestrator = SubmissionOrchestrator(
            self._store, self._broker, self._quotes, compliance_checker, self._notifier, self._clock, self._events,
        )
        self._reconciler = ReconciliationService(self._store, self._broker, self._clock, self._events, self._notifier)

        outcomes = self._reconciler.reconcile(correlation_id="paper-startup")
        logger.info("Paper mode startup reconciliation complete: %d record(s) checked.", len(outcomes))

        self._register_signal_handlers()
        self._notify_best_effort(
            f"[PAPER MODE STARTUP] PaperRunner started. Environment={self._config.environment}. "
            f"Starting cash: {self._starting_cash}. No real broker involved. "
            f"Reconciliation checked {len(outcomes)} record(s)."
        )
        logger.info("Paper mode startup complete.")

    def _build_notifier(self, secrets_config):
        """Takes the raw SecretsConfig, not an already-constructed
        SecretsManager -- constructing SecretsManager itself is now part
        of what this method tries and gracefully degrades from. Found via
        real execution evidence (a real ETF_PLATFORM_MASTER_KEY-missing
        crash on startup): LocalEncryptedFileProvider loads and validates
        the Fernet key at CONSTRUCTION time, not on first get_secret()
        call -- so constructing SecretsManager unconditionally in
        startup() failed before paper mode ever got the chance to decide
        Telegram was optional. Catching SecretsProviderError (the base
        class covering both SecretsBackendUnavailableError -- construction-
        time failures like this one -- and SecretNotFoundError -- a
        successfully-constructed manager missing one specific secret)
        treats both the same way here: no Telegram this session, not a
        startup failure. ProductionRunner is completely untouched by this
        change -- it still constructs SecretsManager unconditionally and
        still fails fast if it can't, exactly as before."""
        try:
            secrets_manager = SecretsManager(secrets_config)
            bot_token = secrets_manager.get_secret(TELEGRAM_BOT_TOKEN_SECRET_NAME)
            chat_id = secrets_manager.get_secret(TELEGRAM_CHAT_ID_SECRET_NAME)
        except SecretsProviderError as exc:
            # Unlike ProductionRunner, paper mode does NOT fail startup
            # over a missing notifier -- Telegram is optional for local
            # development.
            logger.warning(
                "Telegram unavailable this session (%s) -- paper mode will start without notifications. "
                "This is allowed in paper mode; it would NOT be allowed in ProductionRunner.", exc,
            )
            return None
        return TelegramNotificationPort(
            bot_token=bot_token, chat_id=chat_id, transport=self._telegram_transport,
            retry_queue_path=self._telegram_retry_queue_path,
        )

    def _notify_best_effort(self, message):
        if self._notifier is not None:
            self._notifier.send(message)
        else:
            logger.info("(No Telegram notifier configured for this paper session) %s", message)

    def health_check(self):
        try:
            self._broker.get_available_cash()
            return True
        except Exception as exc:
            logger.error("Paper mode health check failed: %s", exc)
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
        logger.info("Paper mode shutting down.")
        self._notify_best_effort("[PAPER MODE SHUTDOWN] PaperRunner shutting down.")
        if self._event_archive is not None and self._events is not None:
            archived_count = self._event_archive.archive_and_clear(self._events)
            logger.info("Archived %d paper-mode event(s) before shutdown.", archived_count)
        if self._store is not None:
            self._store.close()
        logger.info("Paper mode shutdown complete.")
