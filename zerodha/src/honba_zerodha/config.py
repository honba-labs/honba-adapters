"""Configuration dataclass for Zerodha Kite Connect adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from honba.adapters.models import RunMode

from honba_zerodha.constants import ZERODHA_DEFAULT_HTTP_ENDPOINT, ZERODHA_DEFAULT_WS_ENDPOINT


@dataclass(frozen=True, slots=True)
class ZerodhaConfig:
    """Connection parameters and operational limits for Zerodha Kite Connect API."""

    api_key: str = ""
    access_token: str = ""
    api_secret: str = ""
    user_id: str = ""
    mode: RunMode = RunMode.LIVE
    http_endpoint: str = ZERODHA_DEFAULT_HTTP_ENDPOINT
    ws_endpoint: str = ZERODHA_DEFAULT_WS_ENDPOINT
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HONBA_CACHE_DIR", "~/.cache/honba/zerodha")
        ).expanduser()
    )
    # Rate limits in requests per second (Kite v3 limits: 10/s orders, 10/s quotes, 3/s historical)
    order_rate_limit: float = 10.0
    data_rate_limit: float = 10.0
    history_rate_limit: float = 3.0

    @classmethod
    def live(
        cls,
        *,
        api_key: str | None = None,
        access_token: str | None = None,
        cache_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> ZerodhaConfig:
        """Create a configuration for live broker trading."""
        key = api_key or os.environ.get("ZERODHA_API_KEY", "")
        token = access_token or os.environ.get("ZERODHA_ACCESS_TOKEN", "")
        c_dir = Path(cache_dir).expanduser() if cache_dir else None
        args = {
            "api_key": key,
            "access_token": token,
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
        access_token: str = "mock_access_token",
        cache_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> ZerodhaConfig:
        """Create a configuration for paper simulation / sandbox validation."""
        c_dir = Path(cache_dir).expanduser() if cache_dir else None
        args = {
            "api_key": api_key,
            "access_token": access_token,
            "mode": RunMode.PAPER,
            **kwargs,
        }
        if c_dir:
            args["cache_dir"] = c_dir
        return cls(**args)
