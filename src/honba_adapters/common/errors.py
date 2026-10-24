"""Shared error taxonomy for broker adapters (M1).

Re-exports core adapter errors from ``honba.adapters.errors`` and defines shared
specialized error types for rate limiting, authentication, and websocket transport.
"""

from __future__ import annotations

from typing import ClassVar

from honba.adapters.errors import (
    AdapterError,
    AdapterFatalError,
    AdapterNotFound,
    CapabilityError,
    SessionError,
    SessionExpired,
)

__all__ = [
    "AdapterError",
    "AdapterFatalError",
    "AdapterNotFound",
    "AuthenticationError",
    "CapabilityError",
    "RateLimitExceeded",
    "SequenceGapError",
    "SessionError",
    "SessionExpired",
    "TokenExpiredError",
    "WebSocketError",
]


class RateLimitExceeded(AdapterError):
    """Raised when an API call exceeds the configured rate limit quota.

    Args:
        message: Description of the rate limit violation.
        endpoint: Optional name of the rate-limited endpoint.
        retry_after: Optional recommended backoff duration in seconds.
    """

    code: ClassVar[str] = "adapter.rate_limit_exceeded"

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        endpoint: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.retry_after = retry_after


class AuthenticationError(AdapterFatalError):
    """Unrecoverable authentication failure (e.g. invalid API key or secret)."""

    code: ClassVar[str] = "adapter.auth_failed"


class TokenExpiredError(SessionExpired):
    """Session token has expired and cannot be refreshed automatically."""

    code: ClassVar[str] = "adapter.token_expired"


class WebSocketError(AdapterError):
    """Network or protocol failure on a websocket connection."""

    code: ClassVar[str] = "adapter.websocket"


class SequenceGapError(WebSocketError):
    """Detected a sequence number gap in streaming market data or order updates.

    Args:
        message: Description of the sequence gap.
        expected: Expected sequence number.
        received: Actual sequence number received.
    """

    code: ClassVar[str] = "adapter.websocket.sequence_gap"

    def __init__(
        self,
        message: str,
        *,
        expected: int,
        received: int,
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.received = received
