"""Tests for Angel One SmartAPI 2.0 authentication, TOTP login, and token refresh."""

from __future__ import annotations

import pytest

from honba_adapters.angelone.config import AngelOneConfig
from honba_adapters.angelone.http import AngelOneHttpClient
from honba_adapters.angelone.mock import make_angelone_mock_transport
from honba_adapters.common.auth import TOTP, generate_totp


class TestAngelOneAuth:
    @pytest.mark.asyncio
    async def test_totp_code_generation(self) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        code = generate_totp(secret, timestamp=1600000000)
        assert len(code) == 6
        assert code.isdigit()

        totp = TOTP(secret)
        assert totp.verify(code, timestamp=1600000000)

    @pytest.mark.asyncio
    async def test_morning_login_flow(self) -> None:
        cfg = AngelOneConfig(
            api_key="test_api_key",
            client_code="A1001",
            password="test_password",
            totp_key="JBSWY3DPEHPK3PXP",
        )
        transport = make_angelone_mock_transport()
        client = AngelOneHttpClient(cfg, transport=transport)

        res = await client.login_by_password()
        assert res.get("jwtToken") == "mock_jwt_token_valid"
        assert client.jwt_token == "mock_jwt_token_valid"
        assert client.refresh_token == "mock_refresh_token_valid"
        assert client.feed_token == "mock_feed_token_valid"
        await client.close()

    @pytest.mark.asyncio
    async def test_token_refresh_flow(self) -> None:
        cfg = AngelOneConfig(
            api_key="test_api_key",
            client_code="A1001",
            refresh_token="my_existing_refresh_token",
        )
        transport = make_angelone_mock_transport()
        client = AngelOneHttpClient(cfg, transport=transport)

        res = await client.refresh_tokens()
        assert res.get("jwtToken") == "new_refreshed_jwt_token"
        assert client.jwt_token == "new_refreshed_jwt_token"
        await client.close()

    @pytest.mark.asyncio
    async def test_header_injection(self) -> None:
        cfg = AngelOneConfig(
            api_key="test_key_123",
            client_code="A1001",
            jwt_token="valid_jwt",
        )
        client = AngelOneHttpClient(cfg)
        headers = client._build_headers(authenticated=True)

        assert headers["X-PrivateKey"] == "test_key_123"
        assert headers["Authorization"] == "Bearer valid_jwt"
        assert headers["X-SourceID"] == "WEB"
        assert headers["X-UserType"] == "USER"
        assert headers["X-ClientLocalIP"] == "127.0.0.1"
        await client.close()
