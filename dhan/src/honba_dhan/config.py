"""Configuration dataclass and builders for DhanHQ adapter (M2)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from honba.adapters.models import RunMode
from honba_adapters_shared.env import cache_dir, env_mode, missing_credentials_error

from honba_dhan.constants import DEFAULT_DATA_URL, DEFAULT_FEED_URL, DEFAULT_REST_URL

__all__ = ["DhanConfig"]


@dataclass(frozen=True, slots=True)
class DhanConfig:
    """Configuration for connecting to the Dhan broker API."""

    client_id: str
    access_token: str
    rest_url: str = DEFAULT_REST_URL
    feed_url: str = DEFAULT_FEED_URL
    data_url: str = DEFAULT_DATA_URL
    mode: RunMode = RunMode.LIVE
    timeout_seconds: float = 10.0
    orders_per_second: int = 10
    orders_burst: int = 25
    data_per_second: int = 5
    cache_dir: Path = field(default_factory=lambda: cache_dir("dhan"))

    def __post_init__(self) -> None:
        if not self.client_id:
            raise ValueError("client_id cannot be empty")
        if not self.access_token:
            raise ValueError("access_token cannot be empty")

    @classmethod
    def live(
        cls,
        client_id: str,
        access_token: str,
        *,
        cache_dir: Path | str | None = None,
        **kwargs,
    ) -> DhanConfig:
        """Create a live production configuration."""
        c_dir = Path(cache_dir).expanduser() if cache_dir else None
        args = {
            "client_id": client_id,
            "access_token": access_token,
            "mode": RunMode.LIVE,
            **kwargs,
        }
        if c_dir:
            args["cache_dir"] = c_dir
        return cls(**args)

    @classmethod
    def paper(
        cls,
        client_id: str = "PAPER_DHAN_USER",
        access_token: str = "paper_token_valid_for_testing",
        *,
        cache_dir: Path | str | None = None,
        **kwargs,
    ) -> DhanConfig:
        """Create a paper / sandbox configuration."""
        c_dir = Path(cache_dir).expanduser() if cache_dir else None
        args = {
            "client_id": client_id,
            "access_token": access_token,
            "mode": RunMode.PAPER,
            **kwargs,
        }
        if c_dir:
            args["cache_dir"] = c_dir
        return cls(**args)

    @classmethod
    def from_env(cls, prefix: str = "DHAN_", **kwargs) -> DhanConfig:
        """Load configuration from ``<prefix>CLIENT_ID``, ``<prefix>ACCESS_TOKEN``, ``<prefix>MODE``.

        ``MODE`` is ``live`` (default) or ``paper``; paper mode may omit credentials.
        """
        client_id = os.environ.get(f"{prefix}CLIENT_ID", "")
        access_token = os.environ.get(f"{prefix}ACCESS_TOKEN", "")
        mode = env_mode(prefix)
        if mode is RunMode.PAPER:
            creds = {
                k: v for k, v in (("client_id", client_id), ("access_token", access_token)) if v
            }
            return cls.paper(**creds, **kwargs)
        if not client_id or not access_token:
            raise missing_credentials_error(
                "Dhan",
                [f"{prefix}CLIENT_ID", f"{prefix}ACCESS_TOKEN"],
                f"{prefix}MODE=paper or DhanConfig.paper()",
            )
        return cls(client_id=client_id, access_token=access_token, mode=mode, **kwargs)
