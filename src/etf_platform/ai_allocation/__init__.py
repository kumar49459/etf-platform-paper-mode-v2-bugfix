"""AI Allocation hook - interface and disabled default only. The real AI
Dynamic Allocation Engine (roadmap Phase 7) is not implemented here."""

from etf_platform.ai_allocation.ports import AIAllocationPort, AllocationAdjustmentRecord, DisabledAIAllocationPort

__all__ = ["AIAllocationPort", "DisabledAIAllocationPort", "AllocationAdjustmentRecord"]
