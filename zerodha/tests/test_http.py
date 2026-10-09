"""REST client behaviour ported from honba-broker-zerodha ``client.rs`` golden tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest
from honba.adapters.errors import AdapterError, SessionError, SessionExpired
from honba_adapters_shared.errors import AuthenticationError, RateLimitExceeded

from honba_zerodha.config import ZerodhaConfig
from honba_zerodha.http import ZerodhaHttpClient, ZerodhaRejectedError, ZerodhaTransportError

KEY = "kitekey"
TOKEN = "SECRET_ACCESS_TOKEN"


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token: str = TOKEN,
) -> tuple[ZerodhaHttpClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    cfg = ZerodhaConfig.live(api_key=KEY, access_token=token)
    return ZerodhaHttpClient(cfg, transport=httpx.MockTransport(wrapped)), seen


def err_body(kind: str, msg: str) -> dict[str, str]:
    return {"status": "error", "message": msg, "error_type": kind}


def reply(status: int, payload: object = None, text: str | None = None) -> httpx.Response:
    if text is not None:
        return httpx.Response(status, text=text)
    return httpx.Response(status, json=payload)


class TestAuthHeaders:
    async def test_authorization_and_version_headers(self) -> None:
        client, seen = make_client(lambda r: reply(200, {"status": "success", "data": []}))
        await client.get("/orders")
        assert seen[0].headers["Authorization"] == f"token {KEY}:{TOKEN}"
        assert seen[0].headers["X-Kite-Version"] == "3"

    async def test_call_without_session_is_session_error_and_sends_nothing(self) -> None:
        client, seen = make_client(
            lambda r: reply(200, {"status": "success", "data": []}), token=""
        )
        with pytest.raises(SessionError):
            await client.get("/orders")
        assert seen == []


class TestGenerateSession:
    async def test_posts_checksum_and_stores_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return reply(
                200,
                {"status": "success", "data": {"access_token": "newtok", "user_id": "AB1234"}},
            )

        cfg = ZerodhaConfig.live(api_key=KEY, access_token="")
        seen: list[httpx.Request] = []

        def wrapped(r: httpx.Request) -> httpx.Response:
            seen.append(r)
            return handler(r)

        client = ZerodhaHttpClient(cfg, transport=httpx.MockTransport(wrapped))
        user = await client.generate_session("requesttoken", "secret")
        assert user == "AB1234"
        assert client.access_token == "newtok"
        r = seen[0]
        assert r.method == "POST" and r.url.path == "/session/token"
        assert r.headers["X-Kite-Version"] == "3"
        assert "Authorization" not in r.headers
        form = {k: v[0] for k, v in parse_qs(r.content.decode()).items()}
        assert form["api_key"] == KEY
        assert form["request_token"] == "requesttoken"
        # sha256("kitekey" + "requesttoken" + "secret")
        assert form["checksum"] == (
            "592048358eb98402d234059376af6fd55b22e27c5d65695098dcf7c206c333e5"
        )
        assert "secret" not in form.values()
        # The new session authorises later calls.
        await client.get("/orders")
        assert seen[-1].headers["Authorization"] == f"token {KEY}:newtok"

    async def test_session_errors_never_leak_secrets(self) -> None:
        responses = iter(
            [
                reply(200, text="api_secret=topsecret"),
                reply(403, err_body("TokenException", "Invalid checksum")),
            ]
        )
        cfg = ZerodhaConfig.live(api_key=KEY, access_token="")
        client = ZerodhaHttpClient(cfg, transport=httpx.MockTransport(lambda r: next(responses)))
        for _ in range(2):
            with pytest.raises(AdapterError) as exc:
                await client.generate_session("rt", "topsecret")
            text = f"{exc.value!r} {exc.value}"
            assert "topsecret" not in text and "592048" not in text
        assert client.access_token is None


class TestErrorMapping:
    async def _err(self, status: int, body: object = None, text: str | None = None) -> Exception:
        client, _ = make_client(lambda r: reply(status, body, text))
        with pytest.raises(Exception) as exc:
            await client.get("/orders")
        return exc.value

    async def test_token_exception_403_is_session_expired_not_auth_failure(self) -> None:
        e = await self._err(403, err_body("TokenException", "Session expired"))
        assert isinstance(e, SessionExpired)
        assert not isinstance(e, AuthenticationError)
        assert "Session expired" in str(e)

    @pytest.mark.parametrize(
        ("status", "kind"),
        [(400, "InputException"), (400, "OrderException"), (400, "MarginException")],
    )
    async def test_business_exceptions_are_rejected_with_code_and_message(
        self, status: int, kind: str
    ) -> None:
        e = await self._err(status, err_body(kind, "nope"))
        assert isinstance(e, ZerodhaRejectedError)
        assert e.code == kind and e.message == "nope"
        assert kind in str(e) and "nope" in str(e)

    async def test_other_4xx_with_message_is_rejected_with_http_code(self) -> None:
        e = await self._err(404, {"status": "error", "message": "gone"})
        assert isinstance(e, ZerodhaRejectedError)
        assert e.code == "HTTP_404" and e.message == "gone"

    async def test_permission_exception_is_authentication_error(self) -> None:
        e = await self._err(403, err_body("PermissionException", "no"))
        assert isinstance(e, AuthenticationError)

    async def test_rate_limit_and_server_errors(self) -> None:
        assert isinstance(
            await self._err(429, err_body("NetworkException", "slow down")), RateLimitExceeded
        )
        assert isinstance(await self._err(429, text=""), RateLimitExceeded)
        for status, body, text in [
            (503, err_body("NetworkException", "down"), None),
            (500, err_body("GeneralException", "oops"), None),
            (502, None, "<html>bad gateway</html>"),
        ]:
            assert isinstance(await self._err(status, body, text), ZerodhaTransportError)

    async def test_timeout_and_connect_failures_are_transport_errors(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("t")

        client, _ = make_client(boom)
        with pytest.raises(ZerodhaTransportError):
            await client.get("/orders")

        def refused(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client, _ = make_client(refused)
        with pytest.raises(ZerodhaTransportError):
            await client.get("/orders")

    async def test_errors_never_echo_bodies_or_credentials(self) -> None:
        body = f"not json {TOKEN} secret checksum"
        for status in (400, 403, 429, 500):
            e = await self._err(status, text=body)
            text = f"{e!r} {e}"
            assert TOKEN not in text and "secret" not in text, (status, text)

    async def test_json_error_envelope_round_trips_as_json(self) -> None:
        client, _ = make_client(lambda r: reply(200, {"status": "success", "data": [1]}))
        assert await client.get("/orders") == [1]
        assert json.loads("[1]") == [1]


class TestInstrumentsCsv:
    async def test_raw_csv_is_returned_as_text(self) -> None:
        client, seen = make_client(lambda r: reply(200, text="instrument_token,exchange\n1,NSE\n"))
        assert await client.get("/instruments") == "instrument_token,exchange\n1,NSE\n"
        assert seen[0].url.path == "/instruments"
