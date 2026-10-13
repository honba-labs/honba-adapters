"""Credential, env and cache-dir behaviour of the Dhan adapter config."""

from __future__ import annotations

from pathlib import Path

import pytest
from honba.adapters.models import RunMode
from honba_adapters_shared.errors import AuthenticationError

from honba_dhan import DhanAdapter, DhanConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "DHAN_MODE", "HONBA_CACHE_DIR"):
        monkeypatch.delenv(k, raising=False)


def test_bare_adapter_without_credentials_raises() -> None:
    with pytest.raises(AuthenticationError) as ei:
        DhanAdapter()
    msg = str(ei.value)
    assert "DHAN_CLIENT_ID" in msg and "DHAN_ACCESS_TOKEN" in msg
    assert "paper" in msg.lower()


def test_explicit_paper_mode_still_works() -> None:
    a = DhanAdapter(mode="paper")
    assert a._config.mode is RunMode.PAPER
    assert DhanAdapter(DhanConfig.paper())._config.mode is RunMode.PAPER


def test_bare_adapter_uses_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHAN_CLIENT_ID", "c1")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "t1")
    a = DhanAdapter()
    assert a._config.client_id == "c1"
    assert a._config.mode is RunMode.LIVE


def test_from_env_prefix_and_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALT_CLIENT_ID", "c")
    monkeypatch.setenv("ALT_ACCESS_TOKEN", "t")
    monkeypatch.setenv("ALT_MODE", "paper")
    cfg = DhanConfig.from_env(prefix="ALT_")
    assert (cfg.client_id, cfg.access_token, cfg.mode) == ("c", "t", RunMode.PAPER)


def test_from_env_missing_credentials_raises_auth_error() -> None:
    with pytest.raises(AuthenticationError, match="DHAN_CLIENT_ID"):
        DhanConfig.from_env()


def test_from_env_paper_without_credentials_uses_paper_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DHAN_MODE", "paper")
    assert DhanConfig.from_env().mode is RunMode.PAPER


def test_cache_dir_default_and_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert DhanConfig.paper().cache_dir == Path("~/.cache/honba/dhan").expanduser()
    monkeypatch.setenv("HONBA_CACHE_DIR", str(tmp_path))
    assert DhanConfig.paper().cache_dir == tmp_path / "dhan"
