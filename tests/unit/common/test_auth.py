"""Unit tests for authentication, TOTP, and token management (M1)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from honba_adapters.common.auth import (
    TOTP,
    FileTokenStore,
    MemoryTokenStore,
    TokenManager,
    TokenRecord,
    generate_totp,
    verify_totp,
)
from honba_adapters.common.errors import AuthenticationError, TokenExpiredError

# RFC 6238 Appendix B test vector secret: "12345678901234567890" (20 ASCII bytes)
# Base32 representation: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class TestRFC6238TOTP:
    @pytest.mark.parametrize(
        ("timestamp", "expected_code_8", "expected_code_6"),
        [
            (59, "94287082", "287082"),
            (1111111109, "07081804", "081804"),
            (1111111111, "14050471", "050471"),
            (1234567890, "89005924", "005924"),
            (2000000000, "69279037", "279037"),
        ],
    )
    def test_rfc_sha1_test_vectors(
        self, timestamp: int, expected_code_8: str, expected_code_6: str
    ) -> None:
        code8 = generate_totp(RFC_SECRET, timestamp=timestamp, digits=8, digest="sha1")
        assert code8 == expected_code_8

        code6 = generate_totp(RFC_SECRET, timestamp=timestamp, digits=6, digest="sha1")
        assert code6 == expected_code_6

    def test_totp_wrapper_class(self) -> None:
        totp = TOTP(RFC_SECRET, digits=6, interval=30)
        code = totp.at(1234567890)
        assert code == "005924"
        assert totp.verify("005924", timestamp=1234567890) is True
        assert totp.verify("999999", timestamp=1234567890) is False

    def test_totp_drift_window(self) -> None:
        # Step at t=30 is 1, at t=60 is 2, at t=90 is 3
        code_t30 = generate_totp(RFC_SECRET, timestamp=35)
        # Check matching within window=1 at t=65 (step diff = 1)
        assert verify_totp(code_t30, RFC_SECRET, timestamp=65, window=1) is True
        # Outside window=1 at t=95 (step diff = 2)
        assert verify_totp(code_t30, RFC_SECRET, timestamp=95, window=1) is False
        # Matches if window=2
        assert verify_totp(code_t30, RFC_SECRET, timestamp=95, window=2) is True

    def test_time_remaining(self) -> None:
        totp = TOTP(RFC_SECRET, interval=30)
        # At t=10, 20 seconds remain in the 30-second window
        assert totp.time_remaining(10.0) == 20.0
        # At t=29, 1 second remains
        assert totp.time_remaining(29.0) == 1.0

    def test_invalid_parameters(self) -> None:
        with pytest.raises(ValueError, match="Secret cannot be empty"):
            generate_totp("")
        with pytest.raises(ValueError, match="interval must be positive"):
            generate_totp(RFC_SECRET, interval=0)
        with pytest.raises(ValueError, match="digits must be between 1 and 10"):
            generate_totp(RFC_SECRET, digits=0)
        with pytest.raises(ValueError, match="unsupported digest"):
            generate_totp(RFC_SECRET, digest="md5")
        with pytest.raises(ValueError, match="window must be non-negative"):
            verify_totp("123456", RFC_SECRET, window=-1)


class TestTokenRecord:
    def test_unexpiring_token(self) -> None:
        token = TokenRecord(access_token="secret-token")
        assert token.access_token == "secret-token"
        assert token.is_expired() is False
        assert token.time_to_expiry() is None
        assert token.needs_refresh() is False

    def test_token_expiration(self) -> None:
        now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
        future = now + timedelta(minutes=10)
        token = TokenRecord(access_token="tok", expires_at=future)

        assert token.is_expired(now) is False
        assert token.time_to_expiry(now) == timedelta(minutes=10)
        # Inside 15-minute refresh buffer -> needs refresh
        assert token.needs_refresh(buffer_seconds=900, now=now) is True
        # Outside 5-minute refresh buffer -> does not need refresh
        assert token.needs_refresh(buffer_seconds=120, now=now) is False

        # Past expiration
        past = future + timedelta(seconds=1)
        assert token.is_expired(past) is True
        assert token.needs_refresh(now=past) is True

    def test_serialization(self) -> None:
        now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
        record = TokenRecord(
            access_token="tok-123",
            expires_at=now,
            refresh_token="ref-456",
            token_type="Bearer",
            user_id="U1001",
            metadata={"broker": "zerodha"},
        )
        d = record.to_dict()
        restored = TokenRecord.from_dict(d)
        assert restored.access_token == "tok-123"
        assert restored.expires_at == now
        assert restored.refresh_token == "ref-456"
        assert restored.user_id == "U1001"
        assert restored.metadata == {"broker": "zerodha"}

    def test_empty_access_token_raises(self) -> None:
        with pytest.raises(ValueError, match="access_token cannot be empty"):
            TokenRecord(access_token="")


class TestTokenStores:
    def test_memory_token_store(self) -> None:
        store = MemoryTokenStore()
        token = TokenRecord(access_token="test-token")
        assert store.load("client1") is None

        store.save("client1", token)
        assert store.load("client1") == token

        assert store.delete("client1") is True
        assert store.load("client1") is None
        assert store.delete("client1") is False

    def test_file_token_store(self, tmp_path: Path) -> None:
        store = FileTokenStore(tmp_path / "tokens")
        token = TokenRecord(
            access_token="file-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id="USER1",
        )
        assert store.load("user1") is None

        store.save("user1", token)
        loaded = store.load("user1")
        assert loaded is not None
        assert loaded.access_token == "file-token"
        assert loaded.user_id == "USER1"

        # Check file permissions on POSIX
        file_path = store._file_path("user1")
        assert file_path.is_file()
        file_mode = oct(os.stat(file_path).st_mode & 0o777)
        assert file_mode == "0o600"

        # Delete
        assert store.delete("user1") is True
        assert store.load("user1") is None
        assert store.delete("user1") is False


class TestTokenManager:
    @pytest.mark.asyncio
    async def test_get_valid_token(self) -> None:
        store = MemoryTokenStore()
        mgr = TokenManager(store)
        now = datetime.now(timezone.utc)
        valid = TokenRecord(access_token="valid", expires_at=now + timedelta(hours=2))
        mgr.save_token("acc1", valid)

        token = await mgr.get_token("acc1")
        assert token.access_token == "valid"

    @pytest.mark.asyncio
    async def test_auto_refresh_when_expiring(self) -> None:
        store = MemoryTokenStore()
        mgr = TokenManager(store, refresh_buffer_seconds=300)
        now = datetime.now(timezone.utc)
        # Token expires in 60s, buffer is 300s -> needs refresh
        expiring = TokenRecord(access_token="old", expires_at=now + timedelta(seconds=60))
        mgr.save_token("acc1", expiring)

        async def refresher(current: TokenRecord | None) -> TokenRecord:
            assert current is not None
            assert current.access_token == "old"
            return TokenRecord(access_token="new", expires_at=now + timedelta(hours=1))

        refreshed = await mgr.get_token("acc1", refresh_fn=refresher)
        assert refreshed.access_token == "new"
        # Verify saved in store
        saved = store.load("acc1")
        assert saved is not None
        assert saved.access_token == "new"

    @pytest.mark.asyncio
    async def test_missing_token_without_refresher_raises(self) -> None:
        store = MemoryTokenStore()
        mgr = TokenManager(store)
        with pytest.raises(AuthenticationError, match="No token found"):
            await mgr.get_token("missing")

    @pytest.mark.asyncio
    async def test_expired_token_without_refresher_raises(self) -> None:
        store = MemoryTokenStore()
        mgr = TokenManager(store)
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        expired = TokenRecord(access_token="expired", expires_at=past)
        mgr.save_token("acc1", expired)

        with pytest.raises(TokenExpiredError, match="expired"):
            await mgr.get_token("acc1")

    @pytest.mark.asyncio
    async def test_force_refresh(self) -> None:
        store = MemoryTokenStore()
        mgr = TokenManager(store)
        now = datetime.now(timezone.utc)
        valid = TokenRecord(access_token="valid", expires_at=now + timedelta(hours=24))
        mgr.save_token("acc1", valid)

        calls = 0

        async def refresher(current: TokenRecord | None) -> TokenRecord:
            nonlocal calls
            calls += 1
            return TokenRecord(access_token="refreshed")

        token = await mgr.get_token("acc1", refresh_fn=refresher, force_refresh=True)
        assert token.access_token == "refreshed"
        assert calls == 1
