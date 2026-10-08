"""Asynchronous HTTP client for DhanHQ v2 REST API (M2).

Provides rate-limited, authenticated requests with clean error translation.
Supports mock transport injection for deterministic testing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from honba.adapters.errors import AdapterError
from honba_adapters_shared.errors import (
    AuthenticationError,
    RateLimitExceeded,
    SessionExpired,
)
from honba_adapters_shared.rate_limit import RateLimit, RateLimiter

from honba_dhan.config import DhanConfig

__all__ = ["DhanHttpClient"]


class DhanHttpClient:
    """Async HTTP transport layer for DhanHQ REST endpoints."""

    def __init__(
        self,
        config: DhanConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._limiter = RateLimiter(
            {
                "orders": RateLimit(
                    requests=config.orders_per_second,
                    per_seconds=1.0,
                    burst=config.orders_burst,
                ),
                "data": RateLimit(
                    requests=config.data_per_second,
                    per_seconds=1.0,
                ),
            }
        )

        headers = {
            "client-id": config.client_id,
            "access-token": config.access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        self._client = httpx.AsyncClient(
            base_url=config.rest_url.rstrip("/"),
            headers=headers,
            timeout=config.timeout_seconds,
            transport=transport,
        )

    @property
    def config(self) -> DhanConfig:
        return self._config

    @property
    def limiter(self) -> RateLimiter:
        return self._limiter

    async def close(self) -> None:
        """Close underlying HTTP client connections."""
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        pool: str = "data",
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Execute a rate-limited HTTP request against Dhan API."""
        await self._limiter.acquire(pool)

        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
            )
        except httpx.TimeoutException as e:
            raise AdapterError(f"Dhan request timed out for {method} {path}") from e
        except httpx.RequestError as e:
            raise AdapterError(f"Dhan network request failed: {e}") from e

        # Handle HTTP status codes
        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"Dhan authentication failed ({response.status_code}): {response.text}"
            )
        if response.status_code == 429:
            raise RateLimitExceeded(f"Dhan rate limit exceeded: {response.text}", endpoint=pool)

        try:
            data = response.json()
        except json.JSONDecodeError:
            if response.is_error:
                raise AdapterError(f"Dhan HTTP error {response.status_code}: {response.text}")
            return response.text

        # If server returned an HTTP 4xx or 5xx
        if response.is_error:
            # Dhan may return error JSON with remarks
            error_msg = (
                data.get("remarks")
                or data.get("errorMessage")
                or data.get("message")
                or response.text
            )
            # If it's a token expiry indication
            if "TOKEN_EXPIRED" in str(error_msg).upper() or "SESSION" in str(error_msg).upper():
                raise SessionExpired(f"Dhan session expired: {error_msg}")
            # For 4xx/5xx that are not RMS order rejects
            raise AdapterError(f"Dhan API error ({response.status_code}): {error_msg}")

        return data

    async def get(
        self, path: str, *, pool: str = "data", params: Mapping[str, Any] | None = None
    ) -> Any:
        return await self.request("GET", path, pool=pool, params=params)

    async def post(self, path: str, *, pool: str = "orders", json_body: Any = None) -> Any:
        return await self.request("POST", path, pool=pool, json_body=json_body)

    async def put(self, path: str, *, pool: str = "orders", json_body: Any = None) -> Any:
        return await self.request("PUT", path, pool=pool, json_body=json_body)

    async def delete(
        self, path: str, *, pool: str = "orders", params: Mapping[str, Any] | None = None
    ) -> Any:
        return await self.request("DELETE", path, pool=pool, params=params)
