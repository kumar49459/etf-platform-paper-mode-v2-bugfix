"""Portfolio Cash & Execution Manager (Module 28). See
PHASE7_Objectives.md and PHASE7_Design_Readiness_Review.md for the full
design. Milestone 1: foundational models, ports, persistence, timezone
discipline."""


from etf_platform.execution_manager.ambiguous_report import AmbiguousExecutionReport, generate_ambiguous_execution_report
from etf_platform.execution_manager.kite_auth import KiteAuthenticationRequiredError, KiteAuthManager, KiteSession
from etf_platform.execution_manager.kite_broker import KiteBrokerPort, KiteOrderView
from etf_platform.execution_manager.kite_http_client import KiteHTTPClient, KiteHTTPError, KiteHTTPResponse
from etf_platform.execution_manager.kite_status_mapping import UnrecognizedKiteStatusError, map_kite_status
from etf_platform.execution_manager.kite_tag_encoding import TagMappingStore, encode_tag
from etf_platform.execution_manager.clock import Clock, SimulatedClock, SystemClock
from etf_platform.execution_manager.compliance import MinimalInlineComplianceChecker
from etf_platform.execution_manager.events import (
    EventRecorder,
    ExecutionEvent,
    ExecutionEventType,
    InMemoryEventRecorder,
)
from etf_platform.execution_manager.exceptions import (
    BrokerCommunicationError,
    ConcurrentInvocationError,
    DatabaseCorruptionError,
    ExecutionManagerError,
    InvalidLifecycleTransitionError,
    NaiveDatetimeError,
)
from etf_platform.execution_manager.models import (
    ORDER_LIFECYCLE_TRANSITIONS,
    ComplianceCheckResult,
    ComplianceResult,
    CycleClaim,
    ExecutionRecord,
    MarketDepthSnapshot,
    OrderLifecycleState,
    validate_transition,
)
from etf_platform.execution_manager.orchestrator import SubmissionOrchestrator
from etf_platform.execution_manager.paper_broker import OrderRejectedError, PaperBrokerPort, PaperOrder
from etf_platform.execution_manager.paper_quote_provider import PaperQuoteProvider
from etf_platform.execution_manager.persistence import ExecutionStateStore, new_execution_id
from etf_platform.execution_manager.ports import BrokerPort, ComplianceCheckPort, LiveQuoteProvider
from etf_platform.execution_manager.reconciliation import DiscrepancyType, ReconciliationOutcome, ReconciliationService
from etf_platform.execution_manager.scenarios import (
    BrokerScenario,
    FixedScenarioProvider,
    ScenarioParameters,
    ScenarioProvider,
    SeededRandomScenarioProvider,
    SequentialScenarioProvider,
)
from etf_platform.execution_manager.timezone_utils import (
    IST,
    UTC,
    is_within_nse_trading_hours,
    require_aware,
    to_ist,
    to_utc,
    utc_now,
)
from etf_platform.execution_manager.verification import (
    RejectionReason,
    VerificationOutcome,
    VerificationResult,
    VerificationService,
)

__all__ = [
    "OrderLifecycleState",
    "ORDER_LIFECYCLE_TRANSITIONS",
    "validate_transition",
    "ComplianceCheckResult",
    "ComplianceResult",
    "MarketDepthSnapshot",
    "CycleClaim",
    "ExecutionRecord",
    "ExecutionStateStore",
    "new_execution_id",
    "BrokerPort",
    "LiveQuoteProvider",
    "ComplianceCheckPort",
    "UTC",
    "IST",
    "utc_now",
    "require_aware",
    "to_utc",
    "to_ist",
    "is_within_nse_trading_hours",
    "ExecutionManagerError",
    "ConcurrentInvocationError",
    "DatabaseCorruptionError",
    "NaiveDatetimeError",
    "InvalidLifecycleTransitionError",
    "BrokerCommunicationError",
    "Clock",
    "SystemClock",
    "SimulatedClock",
    "EventRecorder",
    "ExecutionEvent",
    "ExecutionEventType",
    "InMemoryEventRecorder",
    "BrokerScenario",
    "ScenarioParameters",
    "ScenarioProvider",
    "FixedScenarioProvider",
    "SequentialScenarioProvider",
    "SeededRandomScenarioProvider",
    "PaperBrokerPort",
    "PaperOrder",
    "OrderRejectedError",
    "PaperQuoteProvider",
    "MinimalInlineComplianceChecker",
    "VerificationService",
    "VerificationResult",
    "VerificationOutcome",
    "RejectionReason",
    "ReconciliationService",
    "ReconciliationOutcome",
    "DiscrepancyType",
    "SubmissionOrchestrator",
    "AmbiguousExecutionReport",
    "generate_ambiguous_execution_report",
    "KiteAuthManager",
    "KiteSession",
    "KiteAuthenticationRequiredError",
    "KiteBrokerPort",
    "KiteOrderView",
    "KiteHTTPClient",
    "KiteHTTPResponse",
    "KiteHTTPError",
    "map_kite_status",
    "UnrecognizedKiteStatusError",
    "encode_tag",
    "TagMappingStore",
]
