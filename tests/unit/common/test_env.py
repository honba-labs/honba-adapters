"""Tests for the shared env helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from honba.adapters.models import RunMode

from honba_adapters.common.env import cache_dir, env_mode, missing_credentials_error


def test_cache_dir_default_is_per_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HONBA_CACHE_DIR", raising=False)
    assert cache_dir("dhan") == Path("~/.cache/honba/dhan").expanduser()


def test_cache_dir_override_is_per_broker_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HONBA_CACHE_DIR", str(tmp_path))
    assert cache_dir("dhan") == tmp_path / "dhan"
    assert cache_dir("zerodha") == tmp_path / "zerodha"


def test_env_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_MODE", raising=False)
    assert env_mode("X_") is RunMode.LIVE
    monkeypatch.setenv("X_MODE", "paper")
    assert env_mode("X_") is RunMode.PAPER
    monkeypatch.setenv("X_MODE", "LIVE")
    assert env_mode("X_") is RunMode.LIVE


def test_missing_credentials_error_names_vars_and_paper() -> None:
    err = missing_credentials_error("Dhan", ["DHAN_CLIENT_ID"], "DhanConfig.paper()")
    assert "DHAN_CLIENT_ID" in str(err)
    assert "DhanConfig.paper()" in str(err)
