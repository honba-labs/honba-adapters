"""Tests for token-bucket rate limiter (M1)."""

from __future__ import annotations

import asyncio

import pytest

from honba_adapters_shared.rate_limit import RateLimit, RateLimiter, TokenBucket, rate_limited


class FakeClock:
    """Injectable monotonic clock for deterministic tests."""

    def __init__(self, initial: float = 0.0) -> None:
        self._now = initial

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += delta


class TestTokenBucket:
    def test_initial_full_pool(self) -> None:
        clock = FakeClock(0.0)
        bucket = TokenBucket(RateLimit(requests=10, per_seconds=1.0), clock=clock)
        assert bucket.try_acquire(5) is True
        assert bucket.try_acquire(5) is True
        assert bucket.try_acquire(1) is False  # exhausted

    def test_refill_over_time(self) -> None:
        clock = FakeClock(0.0)
        bucket = TokenBucket(RateLimit(requests=10, per_seconds=1.0), clock=clock)
        bucket.try_acquire(10)
        assert bucket.try_acquire(1) is False
        clock.advance(0.5)  # half period -> 5 tokens
        assert bucket.try_acquire(5) is True
        assert bucket.try_acquire(1) is False
        clock.advance(1.0)  # full period -> 10 tokens
        assert bucket.try_acquire(10) is True

    def test_burst_allows_extra(self) -> None:
        clock = FakeClock(0.0)
        bucket = TokenBucket(RateLimit(requests=5, per_seconds=1.0, burst=10), clock=clock)
        assert bucket.try_acquire(10) is True  # burst allows full capacity
        assert bucket.try_acquire(1) is False
        clock.advance(1.0)
        assert bucket.try_acquire(5) is True  # refills to steady rate, up to burst

    def test_acquire_async_blocks_until_ready(self) -> None:
        clock = FakeClock(0.0)
        bucket = TokenBucket(RateLimit(requests=5, per_seconds=1.0), clock=clock)
        bucket.try_acquire(5)

        async def run() -> None:
            await bucket.acquire(3)

        clock.advance(1.0)
        asyncio.run(run())

    def test_invalid_rate_limit_args(self) -> None:
        with pytest.raises(ValueError, match="requests must be positive"):
            RateLimit(requests=0, per_seconds=1.0)
        with pytest.raises(ValueError, match="per_seconds must be positive"):
            RateLimit(requests=5, per_seconds=0.0)
        with pytest.raises(ValueError, match="burst must be >= requests"):
            RateLimit(requests=5, per_seconds=1.0, burst=3)
        with pytest.raises(ValueError, match="tokens must be positive"):
            TokenBucket(RateLimit(requests=5, per_seconds=1.0)).try_acquire(0)
        with pytest.raises(ValueError, match="tokens must be positive"):
            TokenBucket(RateLimit(requests=5, per_seconds=1.0)).try_acquire(-1)

    @pytest.mark.asyncio
    async def test_acquire_exceeding_burst_raises(self) -> None:
        bucket = TokenBucket(RateLimit(requests=5, per_seconds=1.0, burst=8))
        with pytest.raises(ValueError, match="cannot exceed burst capacity"):
            await bucket.acquire(9)


class TestRateLimiter:
    def test_per_endpoint_isolation(self) -> None:
        clock = FakeClock(0.0)
        limiter = RateLimiter(
            {
                "orders": RateLimit(requests=10, per_seconds=1.0),
                "quotes": RateLimit(requests=5, per_seconds=1.0),
            },
            clock=clock,
        )
        assert limiter.try_acquire("orders", 10) is True
        assert limiter.try_acquire("quotes", 5) is True
        assert limiter.try_acquire("orders", 1) is False
        assert limiter.try_acquire("quotes", 1) is False

    def test_unknown_endpoint_raises(self) -> None:
        limiter = RateLimiter({"orders": RateLimit(requests=10, per_seconds=1.0)})
        with pytest.raises(KeyError, match="unknown endpoint 'quotes'"):
            limiter.try_acquire("quotes", 1)

    def test_add_limit(self) -> None:
        clock = FakeClock(0.0)
        limiter = RateLimiter({}, clock=clock)
        limiter.add_limit("orders", RateLimit(requests=5, per_seconds=1.0))
        assert "orders" in limiter.endpoints
        assert limiter.try_acquire("orders", 5) is True
        assert limiter.try_acquire("orders", 1) is False

    def test_context_manager(self) -> None:
        clock = FakeClock(0.0)
        limiter = RateLimiter({"test": RateLimit(requests=1, per_seconds=1.0)}, clock=clock)

        async def run() -> None:
            async with rate_limited("test", limiter):
                pass

        asyncio.run(run())
        assert limiter.try_acquire("test", 1) is False  # token consumed

    def test_bucket_accessor(self) -> None:
        clock = FakeClock(0.0)
        limiter = RateLimiter({"orders": RateLimit(requests=10, per_seconds=1.0)}, clock=clock)
        bucket = limiter.bucket("orders")
        assert bucket.try_acquire(5) is True
        assert limiter.try_acquire("orders", 5) is True
        assert limiter.try_acquire("orders", 1) is False


class TestRateLimiterAsync:
    @pytest.mark.asyncio
    async def test_acquire_waits_for_refill(self) -> None:
        clock = FakeClock(0.0)
        limiter = RateLimiter({"orders": RateLimit(requests=2, per_seconds=1.0)}, clock=clock)
        await limiter.acquire("orders", 2)
        task = asyncio.create_task(limiter.acquire("orders", 1))
        await asyncio.sleep(0.01)  # let task start waiting
        assert not task.done()
        clock.advance(1.0)  # refill
        await asyncio.sleep(0.02)
        assert task.done()
