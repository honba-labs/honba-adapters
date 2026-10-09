"""Asynchronous HTTP client for Zerodha Kite Connect v3 REST API."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import httpx
from honba.adapters.errors import AdapterError, SessionError
from honba_adapters_shared.errors import (
    AuthenticationError,
    RateLimitExceeded,
    SessionExpired,
)
from honba_adapters_shared.rate_limit import RateLimit, RateLimiter

from honba_zerodha.config import ZerodhaConfig


class ZerodhaTransportError(AdapterError):
    """The request may or may not have reached Kite: timeout, connection failure or HTTP 5xx.

    The outcome of a state-changing call that raised this is unknown; callers must reconcile
    before retrying (see ``ZerodhaExecutionMixin.place_order``).
    """

    code = "adapter.zerodha.transport"


class ZerodhaRejectedError(AdapterError):
    """Kite refused the request (4xx with a message). Retrying unchanged will not help.

    ``code`` is the Kite ``error_type`` (for example ``OrderException``) or ``HTTP_<status>``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"Zerodha API error ({code}): {message}")
        self.code = code  # type: ignore[misc]
        self.message = message


def _error_fields(response: httpx.Response) -> tuple[str | None, str | None]:
    """Extract Kite ``(error_type, message)`` from a JSON error body, never raw text."""
    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    kind = body.get("error_type")
    message = body.get("message")
    return (
        kind if isinstance(kind, str) else None,
        message if isinstance(message, str) else None,
    )


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
        self._api_key = config.api_key
        self._access_token: str | None = config.access_token or None

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

    @property
    def access_token(self) -> str | None:
        """The current access token, if a session exists."""
        return self._access_token

    def _auth_headers(self) -> dict[str, str]:
        if not (self._api_key and self._access_token):
            raise SessionError("no Kite session; call generate_session")
        return {"Authorization": f"token {self._api_key}:{self._access_token}"}

    async def generate_session(self, request_token: str, api_secret: str) -> str:
        """Exchange a ``request_token`` for an access token, store it and return the user id.

        The checksum is ``sha256(api_key + request_token + api_secret)``; the secret itself is
        never sent and never appears in errors.
        """
        checksum = hashlib.sha256(
            (self._api_key + request_token + api_secret).encode("utf-8")
        ).hexdigest()
        data = await self.request(
            "POST",
            "/session/token",
            pool="data",
            data={
                "api_key": self._api_key,
                "request_token": request_token,
                "checksum": checksum,
            },
            authenticated=False,
        )
        token = data.get("access_token") if isinstance(data, dict) else None
        user_id = data.get("user_id") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token or not isinstance(user_id, str):
            raise AdapterError("Zerodha session response was undecodable")
        self._access_token = token
        return user_id

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
        authenticated: bool = True,
    ) -> Any:
        """Execute rate-limited HTTP request to Kite Connect.

        Error mapping (response bodies and credentials are never echoed):

        - ``TokenException`` (any status): ``SessionExpired`` - the session must be renewed.
        - 401/403 otherwise, ``PermissionException``: ``AuthenticationError``.
        - 429: ``RateLimitExceeded``; timeouts, connection failures and 5xx:
          ``ZerodhaTransportError`` (outcome unknown).
        - other 4xx/``error`` envelopes: ``ZerodhaRejectedError``.
        """
        headers = self._auth_headers() if authenticated else None
        await self._limiter.acquire(pool)

        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
            )
        except httpx.TimeoutException as e:
            raise ZerodhaTransportError(f"Zerodha request timed out for {method} {path}") from e
        except httpx.RequestError as e:
            raise ZerodhaTransportError(
                f"Zerodha network request failed: {type(e).__name__}"
            ) from e

        status_code = response.status_code
        err_type, err_message = _error_fields(response)
        shown = err_message or f"HTTP {status_code}"
        if err_type == "TokenException":
            raise SessionExpired(f"Zerodha session expired: {shown}")
        if status_code in (401, 403):
            raise AuthenticationError(f"Zerodha authentication failed ({status_code}): {shown}")
        if status_code == 429:
            raise RateLimitExceeded(f"Zerodha rate limit exceeded: {shown}", endpoint=pool)
        if status_code >= 500:
            raise ZerodhaTransportError(f"Zerodha server error ({status_code}): {shown}")

        try:
            resp_json = response.json()
        except ValueError:
            if response.is_error:
                raise AdapterError(f"Zerodha HTTP error {status_code}") from None
            return response.text

        if not isinstance(resp_json, dict):
            return resp_json

        # Kite Connect envelops responses in {"status": "success"|"error", "data": ...}
        status = resp_json.get("status")
        if status == "error" or response.is_error:
            error_type = err_type or (
                f"HTTP_{status_code}" if response.is_error else "GeneralException"
            )
            message = err_message or "request failed"

            if error_type == "PermissionException":
                raise AuthenticationError(f"Zerodha permission error: {message}")
            raise ZerodhaRejectedError(error_type, message)

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
