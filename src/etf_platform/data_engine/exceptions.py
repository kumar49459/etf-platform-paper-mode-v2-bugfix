"""Exceptions raised by the Historical Data Engine and its providers."""


class DataEngineError(Exception):
    """Base class for all Data Engine errors."""


class DataProviderError(DataEngineError):
    """Raised when an upstream provider (NSE, Kite) fails to return usable data."""


class RateLimitConfigError(DataEngineError):
    """Raised when a rate limiter is configured with invalid parameters."""


class SymbolResolutionError(DataEngineError):
    """Raised when a symbol cannot be resolved to an instrument token, or when
    the instrument master itself cannot be fetched/parsed."""


class SnapshotNotFoundError(DataEngineError):
    """Raised when a requested data snapshot_id does not exist in the registry."""


class StorageError(DataEngineError):
    """Raised on read/write failures in the underlying storage backend."""
