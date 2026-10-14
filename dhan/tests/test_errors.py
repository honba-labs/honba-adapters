"""REST errorCode mapping: clear messages, fatal classification, no retry (fake HTTP only)."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from honba.adapters.errors import AdapterError, AdapterFatalError
from honba.domain.instrument import InstrumentId

from honba_dhan import DhanAdapter, DhanConfig
from honba_dhan.http import DH902_MESSAGE, DhanHttpClient

RELIANCE = InstrumentId("RELIANCE", "NSE")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _body(code: str, msg: str = "x") -> dict:
    return {"errorType": "Error", "errorCode": code, "errorMessage": msg}


def _client(status: int, body: dict, calls: list[int]) -> DhanHttpClient:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json=body)

    return DhanHttpClient(
        DhanConfig.live(client_id="c", access_token="t"),
        transport=httpx.MockTransport(handler),
    )


async def test_dh902_with_http_401_is_fatal_with_clear_message_and_not_retried() -> None:
    calls: list[int] = []
    c = _client(401, _body("DH-902", "requires a PLUS subscription"), calls)
    with pytest.raises(AdapterFatalError) as ei:
        await c.get("/x")
    assert DH902_MESSAGE in str(ei.value)
    assert "Dhan rejected the token" in DH902_MESSAGE
    assert "DH-902" in DH902_MESSAGE
    assert len(calls) == 1


async def test_dh901_is_fatal() -> None:
    c = _client(401, _body("DH-901"), [])
    with pytest.raises(AdapterFatalError, match="DH-901"):
        await c.get("/x")


async def test_dh905_is_plain_adapter_error_with_hint() -> None:
    c = _client(400, _body("DH-905", "missing field"), [])
    with pytest.raises(AdapterError, match="DH-905") as ei:
        await c.get("/x")
    assert not ei.value.fatal


async def test_dh904_is_rate_limit() -> None:
    from honba_adapters_shared.errors import RateLimitExceeded

    c = _client(429, _body("DH-904"), [])
    with pytest.raises(RateLimitExceeded):
        await c.get("/x")


async def test_plain_401_without_code_still_authentication_error() -> None:
    c = _client(401, {"message": "nope"}, [])
    with pytest.raises(AdapterFatalError, match="authentication failed"):
        await c.get("/x")


async def test_historical_bars_propagates_fatal_dh902() -> None:
    a = DhanAdapter(
        DhanConfig.live(client_id="c", access_token="t"),
        http_transport=httpx.MockTransport(lambda r: httpx.Response(401, json=_body("DH-902"))),
    )
    await a.connect()
    with pytest.raises(AdapterFatalError, match="DH-902"):
        await a.historical_bars(
            RELIANCE,
            timeframe="1m",
            start=dt.datetime(2024, 1, 2, 9, 15, tzinfo=IST),
            end=dt.datetime(2024, 1, 2, 10, 0, tzinfo=IST),
        )


def test_cli_exits_nonzero_with_clear_message_on_fatal(monkeypatch, capsys) -> None:
    import honba_dhan
    import honba_dhan.driver as drv
    from honba_dhan.cli import main
    from honba_dhan.http import DH902_MESSAGE

    class FakeDriver:
        def __init__(self, *a, **k) -> None: ...

        def stop(self) -> None: ...

        async def run(self) -> None:
            from honba_adapters_shared.errors import AuthenticationError

            raise AuthenticationError(DH902_MESSAGE)

    monkeypatch.setattr(honba_dhan, "DhanAdapter", lambda: object())
    monkeypatch.setattr(drv, "DhanBarDriver", FakeDriver)
    monkeypatch.setenv("HONBA_TOKEN", "x")
    assert main(["live", "--symbols", "RELIANCE"]) == 1
    err = capsys.readouterr().err
    assert "Dhan rejected the token; check DHAN_ACCESS_TOKEN" in err
    assert "DH-902" in err
