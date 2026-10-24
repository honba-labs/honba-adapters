"""Tests for AngelOneConfig loading from arguments, paper defaults, and environment variables."""

from __future__ import annotations

import os
from unittest import mock

import pytest
from honba.adapters.models import RunMode

from honba_adapters.angelone.config import AngelOneConfig
from honba_adapters.common.errors import AuthenticationError


class TestAngelOneConfig:
    def test_paper_defaults(self) -> None:
        cfg = AngelOneConfig.paper()
        assert cfg.mode is RunMode.PAPER
        assert cfg.api_key == "mock_api_key"
        assert cfg.client_code == "mock_client_code"
        assert cfg.jwt_token == "mock_jwt_token"
        assert cfg.user_id == "mock_client_code"

    def test_live_explicit(self) -> None:
        cfg = AngelOneConfig.live(
            api_key="my_key",
            client_code="A1234",
            password="secret_password",
            totp_key="JBSWY3DPEHPK3PXP",
        )
        assert cfg.mode is RunMode.LIVE
        assert cfg.api_key == "my_key"
        assert cfg.client_code == "A1234"
        assert cfg.password == "secret_password"
        assert cfg.totp_key == "JBSWY3DPEHPK3PXP"
        assert cfg.user_id == "A1234"

    def test_from_env_paper(self) -> None:
        with mock.patch.dict(os.environ, {"ANGELONE_MODE": "paper"}, clear=True):
            cfg = AngelOneConfig.from_env()
            assert cfg.mode is RunMode.PAPER

    def test_from_env_live(self) -> None:
        env = {
            "ANGELONE_API_KEY": "env_api_key",
            "ANGELONE_CLIENT_CODE": "env_client_code",
            "ANGELONE_PASSWORD": "env_password",
            "ANGELONE_TOTP_KEY": "env_totp_key",
            "ANGELONE_MODE": "live",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = AngelOneConfig.from_env()
            assert cfg.mode is RunMode.LIVE
            assert cfg.api_key == "env_api_key"
            assert cfg.client_code == "env_client_code"
            assert cfg.password == "env_password"
            assert cfg.totp_key == "env_totp_key"

    def test_from_env_live_missing_credentials_raises(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            pytest.raises(AuthenticationError, match="Angel One credentials are missing"),
        ):
            AngelOneConfig.from_env()
