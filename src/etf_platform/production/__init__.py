"""Production Operations (Milestone 7). Sits above execution_manager and
paper_trading_operations, depending on both -- the reverse is never true
(execution_manager must never depend on this package, or on
paper_trading_operations), per this project's established, audited
one-directional dependency rule.
"""
