"""Configuration dataclass for Angel One SmartAPI 2.0 adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from honba.adapters.models import RunMode

from honba_adapters.angelone.constants import (
    ANGELONE_DEFAULT_HTTP_ENDPOINT,
    ANGELONE_DEFAULT_WS_ENDPOINT,
    ANGELONE_ORDER_UPDATE_WS_ENDPOINT,
)
from honba_adapters.common.env import cache_dir, env_mode, missing_credentials_error


@dataclass(frozen=True, slots=True)
class AngelOneConfig:
    """Connection parameters and operational limits for Angel One SmartAPI 2.0."""

    api_key: str = ""
    client_code: str = ""
    password: str = ""
    totp_key: str = ""
    jwt_token: str = ""
    refresh_token: str = ""
    feed_token: str = ""
    user_id: str = ""
    mode: RunMode = RunMode.LIVE
    http_endpoint: str = ANGELONE_DEFAULT_HTTP_ENDPOINT
    ws_endpoint: str = ANGELONE_DEFAULT_WS_ENDPOINT
    order_ws_endpoint: str = ANGELONE_ORDER_UPDATE_WS_ENDPOINT
    cache_dir: Path = field(default_factory=lambda: cache_dir("angelone"))
    # SmartAPI 2.0 rate limits (requests per second)
    order_rate_limit: float = 10.0
    data_rate_limit: float = 10.0
    history_rate_limit: float = 3.0
    # Mandatory SmartAPI header client identifiers
    client_local_ip: str = "127.0.0.1"
    client_public_ip: str = "106.193.147.98"
    mac_address: str = "fe80::1"

    def __post_init__(self) -> None:
        if not self.user_id and self.client_code:
            object.__setattr__(self, "user_id", self.client_code)

    @classmethod
    def live(
        cls,
        *,
        api_key: str | None = None,
        client_code: str | None = None,
        password: str | None = None,
        totp_key: str | None = None,
        jwt_token: str | None = None,
        cache_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> AngelOneConfig:
        """Create a configuration for live broker trading."""
        key = api_key or os.environ.get("ANGELONE_API_KEY", "")
        code = client_code or os.environ.get("ANGELONE_CLIENT_CODE", "")
        pwd = password or os.environ.get("ANGELONE_PASSWORD", "")
        totp = totp_key or os.environ.get("ANGELONE_TOTP_KEY", "")
        jwt = jwt_token or os.environ.get("ANGELONE_JWT_TOKEN", "")

        c_dir = Path(cache_dir).expanduser() if cache_dir else None
        args: dict[str, Any] = {
            "api_key": key,
            "client_code": code,
            "password": pwd,
            "totp_key": totp,
            "jwt_token": jwt,
            "mode": RunMode.LIVE,
            **kwargs,
        }
        if c_dir:
            args["cache_dir"] = c_dir
        return cls(**args)

    @classmethod
    def paper(
        cls,
        *,
        api_key: str = "mock_api_key",
        client_code: str = "mock_client_code",
        password: str = "mock_password",
        totp_key: str = "mock_totp_key",
        jwt_token: str = "mock_jwt_token",
        cache_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> AngelOneConfig:
        """Create a configuration for paper simulation / sandbox validation."""
        c_dir = Path(cache_dir).expanduser() if cache_dir else None
        args: dict[str, Any] = {
            "api_key": api_key,
            "client_code": client_code,
            "password": password,
            "totp_key": totp_key,
            "jwt_token": jwt_token,
            "mode": RunMode.PAPER,
            **kwargs,
        }
        if c_dir:
            args["cache_dir"] = c_dir
        return cls(**args)

    @classmethod
    def from_env(cls, prefix: str = "ANGELONE_", **kwargs: Any) -> AngelOneConfig:
        """Load configuration from environment variables.

        Reads ``<prefix>API_KEY``, ``CLIENT_CODE``, ``PASSWORD``, ``TOTP_KEY``,
        ``JWT_TOKEN``, ``MODE``.
        """
        env = {
            "api_key": os.environ.get(f"{prefix}API_KEY", ""),
            "client_code": os.environ.get(f"{prefix}CLIENT_CODE", ""),
            "password": os.environ.get(f"{prefix}PASSWORD", ""),
            "totp_key": os.environ.get(f"{prefix}TOTP_KEY", ""),
            "jwt_token": os.environ.get(f"{prefix}JWT_TOKEN", ""),
        }
        given = {k: v for k, v in env.items() if v}
        mode = env_mode(prefix)
        if mode is RunMode.PAPER:
            return cls.paper(**given, **kwargs)

        if not env["api_key"] or not env["client_code"]:
            raise missing_credentials_error(
                "Angel One",
                [f"{prefix}API_KEY", f"{prefix}CLIENT_CODE"],
                f"{prefix}MODE=paper or AngelOneConfig.paper()",
            )
        return cls(mode=mode, **given, **kwargs)


__all__ = ["AngelOneConfig"]
