"""Configuration dataclass and builders for DhanHQ adapter (M2)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from honba.adapters.models import RunMode

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
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HONBA_CACHE_DIR", "~/.cache/honba/dhan")
        ).expanduser()
    )

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
        """Load configuration from environment variables."""
        client_id = os.environ.get(f"{prefix}CLIENT_ID", "")
        access_token = os.environ.get(f"{prefix}ACCESS_TOKEN", "")
        mode_str = os.environ.get(f"{prefix}MODE", "LIVE").upper()
        mode = RunMode.PAPER if mode_str == "PAPER" else RunMode.LIVE
        return cls(client_id=client_id, access_token=access_token, mode=mode, **kwargs)
