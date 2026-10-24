"""Environment helpers shared by the broker adapters' config loaders."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from honba.adapters.models import RunMode

from honba_adapters.common.errors import AuthenticationError

__all__ = ["cache_dir", "env_mode", "missing_credentials_error"]


def cache_dir(broker: str) -> Path:
    """Per-broker cache directory.

    ``$HONBA_CACHE_DIR/<broker>`` when the variable is set, else ``~/.cache/honba/<broker>``.
    """
    base = os.environ.get("HONBA_CACHE_DIR")
    root = Path(base).expanduser() if base else Path("~/.cache/honba").expanduser()
    return root / broker


def env_mode(prefix: str) -> RunMode:
    """Run mode from ``<prefix>MODE`` (``paper`` or ``live``, case-insensitive; default live)."""
    return (
        RunMode.PAPER
        if os.environ.get(f"{prefix}MODE", "").strip().lower() == "paper"
        else RunMode.LIVE
    )


def missing_credentials_error(
    broker: str, env_vars: Sequence[str], paper_hint: str
) -> AuthenticationError:
    """Error for a live adapter built without credentials."""
    return AuthenticationError(
        f"{broker} credentials are missing: set {', '.join(env_vars)} (or pass them explicitly). "
        f"For simulation use paper mode explicitly, e.g. {paper_hint}."
    )
