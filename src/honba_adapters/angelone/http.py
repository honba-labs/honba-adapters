"""Asynchronous HTTP client for Angel One SmartAPI 2.0 REST API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx
from honba.adapters.errors import AdapterError, SessionError

from honba_adapters.angelone.config import AngelOneConfig
from honba_adapters.angelone.parsing import map_smartapi_error
from honba_adapters.common.auth import generate_totp
from honba_adapters.common.errors import (
    AuthenticationError,
    RateLimitExceeded,
)
from honba_adapters.common.rate_limit import RateLimit, RateLimiter

logger = logging.getLogger(__name__)


class AngelOneTransportError(AdapterError):
    """Network transport error, timeout, or HTTP 5xx communicating with Angel One."""

    code = "adapter.angelone.transport"


class AngelOneRejectedError(AdapterError):
    """Angel One rejected request with business-logic refusal or client error."""

    code = "adapter.angelone.rejected"

    def __init__(self, broker_code: str, message: str) -> None:
        super().__init__(f"Angel One API error ({broker_code}): {message}")
        self.broker_code = broker_code
        self.message = message


class AngelOneHttpClient:
    """Async HTTP client managing SmartAPI 2.0 authentication, tokens, and rate limits."""

    def __init__(
        self,
        config: AngelOneConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._config = config
        self._base_url = config.http_endpoint.rstrip("/")
        self._api_key = config.api_key
        self._jwt_token: str | None = config.jwt_token or None
        self._refresh_token: str | None = config.refresh_token or None
        self._feed_token: str | None = config.feed_token or None

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            timeout=timeout,
        )

        self._limiter = RateLimiter(
            {
                "orders": RateLimit(
                    requests=int(config.order_rate_limit),
                    per_seconds=1.0,
                    burst=20,
                ),
                "data": RateLimit(
                    requests=int(config.data_rate_limit),
                    per_seconds=1.0,
                    burst=20,
                ),
                "historical": RateLimit(
                    requests=int(config.history_rate_limit),
                    per_seconds=1.0,
                    burst=5,
                ),
            }
        )

    @property
    def jwt_token(self) -> str | None:
        """The current JWT access token."""
        return self._jwt_token

    @property
    def refresh_token(self) -> str | None:
        """The current refresh token."""
        return self._refresh_token

    @property
    def feed_token(self) -> str | None:
        """The current SmartStream feed token."""
        return self._feed_token

    def _build_headers(self, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-PrivateKey": self._api_key,
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self._config.client_local_ip,
            "X-ClientPublicIP": self._config.client_public_ip,
            "X-MACAddress": self._config.mac_address,
        }
        if authenticated:
            if not self._jwt_token:
                raise SessionError(
                    "No SmartAPI JWT session; call login_by_password or set jwt_token"
                )
            headers["Authorization"] = f"Bearer {self._jwt_token}"
        return headers

    async def login_by_password(self) -> dict[str, Any]:
        """Perform headless login with client code, password, and RFC 6238 TOTP."""
        totp_code = ""
        if self._config.totp_key:
            try:
                totp_code = generate_totp(self._config.totp_key)
            except Exception as e:
                raise AuthenticationError(f"Failed to generate TOTP: {e}") from e

        payload = {
            "clientcode": self._config.client_code,
            "password": self._config.password,
            "totp": totp_code,
        }

        res = await self.request(
            "POST",
            "/rest/auth/angelbroking/user/v1/loginByPassword",
            pool="data",
            json_body=payload,
            authenticated=False,
        )
        if isinstance(res, dict):
            self._jwt_token = res.get("jwtToken") or self._jwt_token
            self._refresh_token = res.get("refreshToken") or self._refresh_token
            self._feed_token = res.get("feedToken") or self._feed_token
        return res if isinstance(res, dict) else {}

    async def refresh_tokens(self) -> dict[str, Any]:
        """Renew expired JWT session token using refresh token."""
        if not self._refresh_token:
            raise AuthenticationError("No refresh token available to renew SmartAPI session")

        payload = {"refreshToken": self._refresh_token}
        res = await self.request(
            "POST",
            "/rest/auth/angelbroking/jwt/v1/generateTokens",
            pool="data",
            json_body=payload,
            authenticated=False,
        )
        if isinstance(res, dict):
            self._jwt_token = res.get("jwtToken") or self._jwt_token
            self._refresh_token = res.get("refreshToken") or self._refresh_token
            self._feed_token = res.get("feedToken") or self._feed_token
        return res if isinstance(res, dict) else {}

    async def close(self) -> None:
        """Close HTTP client connections."""
        await self._client.aclose()

    async def aclose(self) -> None:
        """Close HTTP client connections."""
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        pool: str = "data",
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        authenticated: bool = True,
        retry_on_auth: bool = True,
    ) -> Any:
        """Execute rate-limited HTTP request to Angel One SmartAPI."""
        headers = self._build_headers(authenticated=authenticated)
        await self._limiter.acquire(pool)

        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.TimeoutException as e:
            raise AngelOneTransportError(f"Angel One request timed out: {method} {path}") from e
        except httpx.RequestError as e:
            raise AngelOneTransportError(
                f"Angel One network request failed: {type(e).__name__}"
            ) from e

        status_code = response.status_code

        # Attempt token refresh on 401
        if status_code in (401, 403) and authenticated and retry_on_auth and self._refresh_token:
            try:
                await self.refresh_tokens()
                return await self.request(
                    method,
                    path,
                    pool=pool,
                    params=params,
                    json_body=json_body,
                    authenticated=True,
                    retry_on_auth=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Automatic token refresh failed after 401: %s", exc)

        if status_code == 429:
            raise RateLimitExceeded(
                f"Angel One rate limit exceeded: HTTP {status_code}", endpoint=pool
            )
        if status_code >= 500:
            raise AngelOneTransportError(f"Angel One server error ({status_code})")

        try:
            resp_json = response.json()
        except ValueError:
            if response.is_error:
                raise AngelOneTransportError(f"Non-JSON error reply (HTTP {status_code})")
            return response.text

        if not isinstance(resp_json, dict):
            return resp_json

        status_flag = resp_json.get("status")
        # SmartAPI envelops status as bool: True / False
        if status_flag is False or response.is_error:
            message = str(resp_json.get("message") or "SmartAPI error")
            code = str(resp_json.get("errorcode") or "")
            err = map_smartapi_error(status_code, code, message)

            if (
                isinstance(err, AuthenticationError)
                and authenticated
                and retry_on_auth
                and self._refresh_token
            ):
                try:
                    await self.refresh_tokens()
                    return await self.request(
                        method,
                        path,
                        pool=pool,
                        params=params,
                        json_body=json_body,
                        authenticated=True,
                        retry_on_auth=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Automatic token refresh failed after error: %s", exc)

            if isinstance(err, (AuthenticationError, RateLimitExceeded)):
                raise err
            raise AngelOneRejectedError(code or f"HTTP_{status_code}", message)

        return resp_json.get("data", resp_json)

    async def get(
        self,
        path: str,
        *,
        pool: str = "data",
        params: Mapping[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        return await self.request(
            "GET", path, pool=pool, params=params, authenticated=authenticated
        )

    async def post(
        self,
        path: str,
        *,
        pool: str = "data",
        json_body: Any = None,
        authenticated: bool = True,
    ) -> Any:
        return await self.request(
            "POST", path, pool=pool, json_body=json_body, authenticated=authenticated
        )

    async def put(
        self,
        path: str,
        *,
        pool: str = "data",
        json_body: Any = None,
        authenticated: bool = True,
    ) -> Any:
        return await self.request(
            "PUT", path, pool=pool, json_body=json_body, authenticated=authenticated
        )


__all__ = [
    "AngelOneHttpClient",
    "AngelOneRejectedError",
    "AngelOneTransportError",
]
