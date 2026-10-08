"""Asynchronous HTTP client for Zerodha Kite Connect v3 REST API."""

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

from honba_zerodha.config import ZerodhaConfig


class ZerodhaHttpClient:
    """Async HTTP client managing Zerodha Kite v3 token headers, pools, and rate limits."""

    def __init__(
        self,
        config: ZerodhaConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._config = config
        self._base_url = config.http_endpoint.rstrip("/")
        headers = {
            "X-Kite-Version": "3",
            "User-Agent": "honba-zerodha/0.1.0",
        }
        if config.api_key and config.access_token:
            headers["Authorization"] = f"token {config.api_key}:{config.access_token}"

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            transport=transport,
            timeout=timeout,
        )

        # Rate limit pools per Kite Connect v3 rules
        self._limiter = RateLimiter(
            {
                "orders": RateLimit(
                    requests=config.order_rate_limit,
                    per_seconds=1.0,
                    burst=20,
                ),
                "data": RateLimit(
                    requests=config.data_rate_limit,
                    per_seconds=1.0,
                    burst=20,
                ),
                "historical": RateLimit(
                    requests=config.history_rate_limit,
                    per_seconds=1.0,
                    burst=5,
                ),
            }
        )

    async def aclose(self) -> None:
        """Close underlying HTTP client connections."""
        await self._client.aclose()

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
        data: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Execute rate-limited HTTP request to Kite Connect."""
        await self._limiter.acquire(pool)

        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                data=data,
                json=json_body,
            )
        except httpx.TimeoutException as e:
            raise AdapterError(f"Zerodha request timed out for {method} {path}") from e
        except httpx.RequestError as e:
            raise AdapterError(f"Zerodha network request failed: {e}") from e

        # Handle HTTP status codes
        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"Zerodha authentication failed ({response.status_code}): {response.text}"
            )
        if response.status_code == 429:
            raise RateLimitExceeded(f"Zerodha rate limit exceeded: {response.text}", endpoint=pool)

        try:
            resp_json = response.json()
        except json.JSONDecodeError:
            if response.is_error:
                raise AdapterError(f"Zerodha HTTP error {response.status_code}: {response.text}")
            return response.text

        # Kite Connect envelops responses in {"status": "success"|"error", "data": ...}
        status = resp_json.get("status")
        if status == "error" or response.is_error:
            error_type = resp_json.get("error_type", "GeneralException")
            message = resp_json.get("message", response.text)

            if error_type == "TokenException" or "token" in str(message).lower():
                raise SessionExpired(f"Zerodha session expired: {message}")
            if error_type == "PermissionException":
                raise AuthenticationError(f"Zerodha permission error: {message}")
            raise AdapterError(f"Zerodha API error ({error_type}): {message}")

        # Return data payload or top-level dictionary
        return resp_json.get("data", resp_json)

    async def get(
        self,
        path: str,
        *,
        pool: str = "data",
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        return await self.request("GET", path, pool=pool, params=params)

    async def post(
        self,
        path: str,
        *,
        pool: str = "orders",
        data: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        return await self.request("POST", path, pool=pool, data=data, json_body=json_body)

    async def put(
        self,
        path: str,
        *,
        pool: str = "orders",
        data: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        return await self.request("PUT", path, pool=pool, data=data, json_body=json_body)

    async def delete(
        self,
        path: str,
        *,
        pool: str = "orders",
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        return await self.request("DELETE", path, pool=pool, params=params)
