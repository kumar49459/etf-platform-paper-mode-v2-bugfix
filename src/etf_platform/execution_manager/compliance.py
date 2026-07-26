"""MinimalInlineComplianceChecker (PHASE7_Objectives.md section 8.4,
Decision 3): exactly the two narrow checks approved for this phase --
static IP verification and Algo ID tagging. No additional compliance
logic. Future requirements belong in Module 24, wired in as a second
implementation of ComplianceCheckPort -- this class's existence is what
makes that swap possible without touching Module 28's core.
"""

from __future__ import annotations

from etf_platform.execution_manager.models import ComplianceCheckResult, ComplianceResult
from etf_platform.execution_manager.ports import ComplianceCheckPort


class MinimalInlineComplianceChecker(ComplianceCheckPort):
    def __init__(self, static_ip_verified=True, algo_id="ETF-PLATFORM-01"):
        self._static_ip_verified = static_ip_verified
        self._algo_id = algo_id

    def check(self, symbol, quantity, limit_price):
        if not self._static_ip_verified:
            return ComplianceResult(result=ComplianceCheckResult.FAIL, reason="Static IP not verified.")
        if not self._algo_id:
            return ComplianceResult(result=ComplianceCheckResult.FAIL, reason="Algo ID tag missing.")
        return ComplianceResult(result=ComplianceCheckResult.PASS, reason=f"Algo ID {self._algo_id} tagged.")
