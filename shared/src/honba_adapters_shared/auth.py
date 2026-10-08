"""Authentication and credential management infrastructure (M1).

Provides:
- Standard RFC 6238 TOTP generation and verification for headless broker logins.
- Token records with expiration and refresh readiness detection.
- Abstract and concrete token stores (Memory and atomic FileTokenStore).
- Asynchronous TokenManager coordinating token retrieval, freshness checks, and auto-refresh.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import struct
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from honba_adapters_shared.errors import AuthenticationError, TokenExpiredError

__all__ = [
    "TOTP",
    "FileTokenStore",
    "MemoryTokenStore",
    "TokenManager",
    "TokenRecord",
    "TokenStore",
    "generate_totp",
    "verify_totp",
]

_DIGEST_MODS: dict[str, Any] = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}


def _clean_base32_secret(secret: str) -> bytes:
    """Normalize and decode a base32 encoded secret string."""
    cleaned = secret.strip().replace(" ", "").replace("-", "").upper()
    if not cleaned:
        raise ValueError("Secret cannot be empty")
    missing_padding = len(cleaned) % 8
    if missing_padding:
        cleaned += "=" * (8 - missing_padding)
    try:
        return base64.b32decode(cleaned, casefold=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"Invalid base32 secret: {e}") from e


def generate_totp(
    secret: str,
    *,
    timestamp: float | None = None,
    interval: int = 30,
    digits: int = 6,
    digest: str = "sha1",
) -> str:
    """Generate an RFC 6238 TOTP code.

    Args:
        secret: Base32 encoded secret key.
        timestamp: Monotonic or POSIX timestamp (defaults to current time).
        interval: Time step window in seconds (default 30).
        digits: Number of digits in generated code (default 6).
        digest: Hash algorithm ('sha1', 'sha256', 'sha512').

    Returns:
        Formatted numeric TOTP string with leading zeros.
    """
    if interval <= 0:
        raise ValueError("interval must be positive")
    if digits <= 0 or digits > 10:
        raise ValueError("digits must be between 1 and 10")
    digest_mod = _DIGEST_MODS.get(digest.lower())
    if digest_mod is None:
        raise ValueError(
            f"unsupported digest algorithm '{digest}'; supported: {list(_DIGEST_MODS.keys())}"
        )

    key_bytes = _clean_base32_secret(secret)
    t = time.time() if timestamp is None else float(timestamp)
    counter = math.floor(t / interval)
    counter_bytes = struct.pack(">Q", counter)

    hmac_digest = hmac.new(key_bytes, counter_bytes, digest_mod).digest()
    offset = hmac_digest[-1] & 0x0F
    code = struct.unpack(">I", hmac_digest[offset : offset + 4])[0] & 0x7FFFFFFF
    otp = code % (10**digits)
    return f"{otp:0{digits}d}"


def verify_totp(
    otp: str,
    secret: str,
    *,
    timestamp: float | None = None,
    interval: int = 30,
    digits: int = 6,
    digest: str = "sha1",
    window: int = 1,
) -> bool:
    """Verify a TOTP code within a drift window.

    Args:
        otp: Candidate TOTP string.
        secret: Base32 encoded secret key.
        timestamp: Monotonic or POSIX timestamp (defaults to current time).
        interval: Time step window in seconds.
        digits: Expected number of digits.
        digest: Hash algorithm.
        window: Number of intervals to check before and after (default 1).

    Returns:
        True if the code matches within the window, False otherwise.
    """
    if window < 0:
        raise ValueError("window must be non-negative")
    t = time.time() if timestamp is None else float(timestamp)

    for step in range(-window, window + 1):
        step_time = t + (step * interval)
        expected = generate_totp(
            secret,
            timestamp=step_time,
            interval=interval,
            digits=digits,
            digest=digest,
        )
        if hmac.compare_digest(otp.strip(), expected):
            return True
    return False


@dataclass(frozen=True, slots=True)
class TOTP:
    """Convenience class encapsulating TOTP parameters for a secret."""

    secret: str
    interval: int = 30
    digits: int = 6
    digest: str = "sha1"

    def now(self) -> str:
        """Generate TOTP code for the current moment."""
        return generate_totp(
            self.secret,
            interval=self.interval,
            digits=self.digits,
            digest=self.digest,
        )

    def at(self, timestamp: float) -> str:
        """Generate TOTP code for an explicit timestamp."""
        return generate_totp(
            self.secret,
            timestamp=timestamp,
            interval=self.interval,
            digits=self.digits,
            digest=self.digest,
        )

    def verify(self, otp: str, *, timestamp: float | None = None, window: int = 1) -> bool:
        """Verify code against this TOTP configuration."""
        return verify_totp(
            otp,
            self.secret,
            timestamp=timestamp,
            interval=self.interval,
            digits=self.digits,
            digest=self.digest,
            window=window,
        )

    def time_remaining(self, timestamp: float | None = None) -> float:
        """Seconds remaining in the current time step."""
        t = time.time() if timestamp is None else float(timestamp)
        return float(self.interval - (t % self.interval))


@dataclass(frozen=True, slots=True)
class TokenRecord:
    """Represents an authenticated broker session token.

    Attributes:
        access_token: The primary bearer / API access token.
        expires_at: Optional UTC datetime when this token expires.
        refresh_token: Optional refresh token for renewal.
        token_type: Token type header prefix (default 'Bearer').
        user_id: Optional broker user or client ID.
        created_at: Datetime when token was minted or stored.
        metadata: Additional arbitrary broker session data.
    """

    access_token: str
    expires_at: datetime | None = None
    refresh_token: str | None = None
    token_type: str = "Bearer"
    user_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.access_token:
            raise ValueError("access_token cannot be empty")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            object.__setattr__(
                self, "expires_at", self.expires_at.replace(tzinfo=timezone.utc)
            )

    def is_expired(self, now: datetime | None = None) -> bool:
        """Check if the token has reached or passed its expiration time."""
        if self.expires_at is None:
            return False
        current = now if now is not None else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current >= self.expires_at

    def time_to_expiry(self, now: datetime | None = None) -> timedelta | None:
        """Time remaining until expiration, or None if token never expires."""
        if self.expires_at is None:
            return None
        current = now if now is not None else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return self.expires_at - current

    def needs_refresh(self, buffer_seconds: float = 300.0, now: datetime | None = None) -> bool:
        """Check if token is expired or within the refresh warning buffer."""
        if self.expires_at is None:
            return False
        remaining = self.time_to_expiry(now)
        assert remaining is not None
        return remaining <= timedelta(seconds=buffer_seconds)

    def to_dict(self) -> dict[str, Any]:
        """Serialize token record to JSON-compatible dictionary."""
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TokenRecord:
        """Deserialize token record from dictionary."""
        expires_at = None
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
        created_at = datetime.now(timezone.utc)
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])
        return cls(
            access_token=data["access_token"],
            expires_at=expires_at,
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type", "Bearer"),
            user_id=data.get("user_id"),
            created_at=created_at,
            metadata=dict(data.get("metadata", {})),
        )


@runtime_checkable
class TokenStore(Protocol):
    """Protocol for persisting and loading broker access tokens."""

    def save(self, key: str, token: TokenRecord) -> None:
        """Save a token record under a key."""
        ...

    def load(self, key: str) -> TokenRecord | None:
        """Load a token record by key, or return None if not found."""
        ...

    def delete(self, key: str) -> bool:
        """Delete a token record. Returns True if deleted, False if not found."""
        ...


class MemoryTokenStore:
    """In-memory token store for testing and short-lived sessions."""

    def __init__(self) -> None:
        self._tokens: dict[str, TokenRecord] = {}

    def save(self, key: str, token: TokenRecord) -> None:
        self._tokens[key] = token

    def load(self, key: str) -> TokenRecord | None:
        return self._tokens.get(key)

    def delete(self, key: str) -> bool:
        return self._tokens.pop(key, None) is not None


class FileTokenStore:
    """Secure atomic file-backed token store.

    Saves tokens in JSON files under ``directory``. On POSIX filesystems,
    enforces 0o600 permissions to keep secrets private.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory).expanduser().resolve()
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def _file_path(self, key: str) -> Path:
        safe_key = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in key)
        return self._dir / f"{safe_key}.json"

    def save(self, key: str, token: TokenRecord) -> None:
        path = self._file_path(key)
        data = json.dumps(token.to_dict(), indent=2).encode("utf-8")

        fd, tmp_path = tempfile.mkstemp(dir=self._dir, prefix=".token_tmp_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load(self, key: str) -> TokenRecord | None:
        path = self._file_path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TokenRecord.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def delete(self, key: str) -> bool:
        path = self._file_path(key)
        if path.is_file():
            path.unlink()
            return True
        return False


class TokenManager:
    """Coordinates token loading, expiration evaluation, and automatic refresh."""

    def __init__(
        self,
        store: TokenStore,
        *,
        refresh_buffer_seconds: float = 300.0,
    ) -> None:
        self._store = store
        self._refresh_buffer_seconds = refresh_buffer_seconds

    @property
    def store(self) -> TokenStore:
        return self._store

    def save_token(self, key: str, token: TokenRecord) -> None:
        self._store.save(key, token)

    def delete_token(self, key: str) -> bool:
        return self._store.delete(key)

    async def get_token(
        self,
        key: str,
        *,
        refresh_fn: Callable[[TokenRecord | None], Awaitable[TokenRecord]] | None = None,
        force_refresh: bool = False,
    ) -> TokenRecord:
        """Retrieve a valid token, refreshing if necessary.

        Args:
            key: Identifier for the token (e.g. broker user id or account name).
            refresh_fn: Optional async function to refresh or acquire new token.
            force_refresh: If True, triggers refresh regardless of current token expiry.

        Returns:
            Valid TokenRecord.

        Raises:
            TokenExpiredError: If token is expired and no refresh_fn is provided or refresh fails.
            AuthenticationError: If token does not exist and no refresh_fn is provided.
        """
        token = self._store.load(key)

        needs_renewal = (
            force_refresh
            or token is None
            or token.needs_refresh(buffer_seconds=self._refresh_buffer_seconds)
        )

        if not needs_renewal and token is not None:
            return token

        if refresh_fn is None:
            if token is None:
                raise AuthenticationError(
                    f"No token found for key '{key}' and no refresh_fn provided"
                )
            if token.is_expired():
                raise TokenExpiredError(
                    f"Token '{key}' expired at {token.expires_at} and cannot refresh"
                )
            return token

        try:
            new_token = await refresh_fn(token)
        except Exception as e:
            if token is not None and not token.is_expired():
                return token
            raise TokenExpiredError(f"Failed to refresh token for '{key}': {e}") from e

        self._store.save(key, new_token)
        return new_token
