"""Token-bucket rate limiter with per-endpoint pools (M1).

Provides deterministic, testable rate limiting for broker API calls. Uses an injectable
monotonic clock and optional sleep coroutine so unit tests can advance time instantly
without real sleeps.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

__all__ = ["RateLimit", "RateLimiter", "TokenBucket", "rate_limited"]


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Rate limit specification for one endpoint pool.

    Args:
        requests: Number of requests allowed per period (steady-state rate).
        per_seconds: Period length in seconds.
        burst: Maximum burst capacity (tokens available at once). Defaults to ``requests``.
    """

    requests: int
    per_seconds: float
    burst: int | None = None

    def __post_init__(self) -> None:
        if self.requests <= 0:
            raise ValueError("requests must be positive")
        if self.per_seconds <= 0:
            raise ValueError("per_seconds must be positive")
        burst = self.burst if self.burst is not None else self.requests
        if burst < self.requests:
            raise ValueError("burst must be >= requests")
        object.__setattr__(self, "burst", burst)

    @property
    def rate_per_second(self) -> float:
        return self.requests / self.per_seconds


class TokenBucket:
    """A single token bucket with injectable clock and sleep function."""

    def __init__(
        self,
        limit: RateLimit,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._limit = limit
        self._burst: int = limit.burst if limit.burst is not None else limit.requests
        self._is_custom_clock = clock is not None and clock is not time.monotonic
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._tokens: float = float(self._burst)
        self._last_refill = self._clock()

    @property
    def limit(self) -> RateLimit:
        return self._limit

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            new_tokens = elapsed * self._limit.rate_per_second
            self._tokens = min(float(self._burst), self._tokens + new_tokens)
            self._last_refill = now

    def try_acquire(self, tokens: int = 1, *, now: float | None = None) -> bool:
        """Non-blocking attempt to acquire tokens.

        Args:
            tokens: Number of tokens to acquire (must be positive).
            now: Optional explicit timestamp for the operation (useful for tests).

        Returns:
            True if tokens were available and consumed, False otherwise.
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if now is not None:
            elapsed = now - self._last_refill
            if elapsed > 0:
                new_tokens = elapsed * self._limit.rate_per_second
                self._tokens = min(float(self._burst), self._tokens + new_tokens)
                self._last_refill = now
        else:
            self._refill()

        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def acquire(self, tokens: int = 1) -> None:
        """Blocking acquisition: waits until tokens are available.

        Args:
            tokens: Number of tokens to acquire (must be positive).
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if tokens > self._burst:
            raise ValueError(f"tokens ({tokens}) cannot exceed burst capacity ({self._burst})")

        while True:
            if self.try_acquire(tokens):
                return
            self._refill()
            deficit = tokens - self._tokens
            wait_time = deficit / self._limit.rate_per_second
            if self._is_custom_clock:
                # Polling interval when running under simulated clock
                wait_time = min(wait_time, 0.005)
            await self._sleep(max(wait_time, 0.001))


class RateLimiter:
    """Manages named per-endpoint token buckets."""

    def __init__(
        self,
        limits: Mapping[str, RateLimit],
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._buckets: dict[str, TokenBucket] = {
            name: TokenBucket(limit, clock=self._clock, sleep=self._sleep)
            for name, limit in limits.items()
        }

    @property
    def endpoints(self) -> tuple[str, ...]:
        return tuple(self._buckets.keys())

    def bucket(self, endpoint: str) -> TokenBucket:
        """Get the token bucket for an endpoint."""
        try:
            return self._buckets[endpoint]
        except KeyError as e:
            raise KeyError(f"unknown endpoint '{endpoint}'") from e

    def add_limit(self, endpoint: str, limit: RateLimit) -> None:
        """Add or replace a rate limit pool for an endpoint."""
        self._buckets[endpoint] = TokenBucket(limit, clock=self._clock, sleep=self._sleep)

    def try_acquire(self, endpoint: str, tokens: int = 1) -> bool:
        """Non-blocking acquire from an endpoint's bucket."""
        return self.bucket(endpoint).try_acquire(tokens)

    async def acquire(self, endpoint: str, tokens: int = 1) -> None:
        """Blocking acquire from an endpoint's bucket."""
        await self.bucket(endpoint).acquire(tokens)


@asynccontextmanager
async def rate_limited(endpoint: str, limiter: RateLimiter, tokens: int = 1):
    """Async context manager that acquires rate limit tokens on entry.

    Usage:
        async with rate_limited("orders", limiter):
            await place_order(...)
    """
    await limiter.acquire(endpoint, tokens)
    try:
        yield
    finally:
        pass
