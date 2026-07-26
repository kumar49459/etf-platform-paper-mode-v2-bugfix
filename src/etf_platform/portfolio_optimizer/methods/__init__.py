"""Pluggable allocation methods. Importing this package registers every
built-in method (currently: inverse-volatility). Adding a new method later
means adding one file here plus one registration call -- no changes
anywhere else in the platform.
"""

from etf_platform.portfolio_optimizer.methods.base import (
    AllocationMethod,
    get_method,
    register_method,
    registered_methods,
)
from etf_platform.portfolio_optimizer.methods.inverse_volatility import InverseVolatilityMethod

register_method(InverseVolatilityMethod())

__all__ = ["AllocationMethod", "register_method", "get_method", "registered_methods", "InverseVolatilityMethod"]
