"""Credential, env and cache-dir behaviour of the Zerodha adapter config."""

from __future__ import annotations

from pathlib import Path

import pytest
from honba.adapters.models import RunMode

from honba_adapters.common.errors import AuthenticationError
from honba_adapters.zerodha import ZerodhaAdapter, ZerodhaConfig

_VARS = (
    "ZERODHA_API_KEY",
    "ZERODHA_ACCESS_TOKEN",
    "ZERODHA_API_SECRET",
    "ZERODHA_USER_ID",
    "ZERODHA_MODE",
    "HONBA_CACHE_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _VARS:
        monkeypatch.delenv(k, raising=False)


def test_bare_adapter_without_credentials_raises() -> None:
    with pytest.raises(AuthenticationError) as ei:
        ZerodhaAdapter()
    msg = str(ei.value)
    assert "ZERODHA_API_KEY" in msg and "ZERODHA_ACCESS_TOKEN" in msg
    assert "paper" in msg.lower()


def test_explicit_paper_mode_still_works() -> None:
    assert ZerodhaAdapter(mode="paper")._config.mode is RunMode.PAPER
    assert ZerodhaAdapter(ZerodhaConfig.paper())._config.mode is RunMode.PAPER


def test_bare_adapter_uses_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZERODHA_API_KEY", "k")
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
    a = ZerodhaAdapter()
    assert a._config.api_key == "k"
    assert a._config.mode is RunMode.LIVE


def test_from_env_default_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZERODHA_API_KEY", "k")
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
    monkeypatch.setenv("ZERODHA_API_SECRET", "s")
    cfg = ZerodhaConfig.from_env()
    assert (cfg.api_key, cfg.access_token, cfg.api_secret) == ("k", "t", "s")
    assert cfg.mode is RunMode.LIVE


def test_from_env_prefix_and_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALT_API_KEY", "k")
    monkeypatch.setenv("ALT_ACCESS_TOKEN", "t")
    monkeypatch.setenv("ALT_MODE", "paper")
    cfg = ZerodhaConfig.from_env(prefix="ALT_")
    assert (cfg.api_key, cfg.mode) == ("k", RunMode.PAPER)


def test_from_env_missing_credentials_raises_auth_error() -> None:
    with pytest.raises(AuthenticationError, match="ZERODHA_API_KEY"):
        ZerodhaConfig.from_env()


def test_from_env_paper_without_credentials_uses_paper_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZERODHA_MODE", "paper")
    assert ZerodhaConfig.from_env().mode is RunMode.PAPER


def test_cache_dir_default_and_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert ZerodhaConfig.paper().cache_dir == Path("~/.cache/honba/zerodha").expanduser()
    monkeypatch.setenv("HONBA_CACHE_DIR", str(tmp_path))
    assert ZerodhaConfig.paper().cache_dir == tmp_path / "zerodha"
