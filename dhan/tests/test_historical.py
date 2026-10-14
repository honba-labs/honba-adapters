"""historical_bars: request payload shape and closed-bar filtering (fake HTTP only)."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
from honba.adapters.errors import AdapterError
from honba.domain.instrument import InstrumentId

from honba_dhan import DhanAdapter, DhanConfig

RELIANCE = InstrumentId("RELIANCE", "NSE")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# 2024-01-02 09:15 IST == 03:45 UTC
T0 = int(dt.datetime(2024, 1, 2, 9, 15, tzinfo=IST).timestamp())


def _resp(n: int, step: int = 60) -> dict:
    return {
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [10.0 * (i + 1) for i in range(n)],
        "timestamp": [float(T0 + i * step) for i in range(n)],
    }


async def _adapter(body: dict, seen: list[httpx.Request]) -> DhanAdapter:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=body)

    a = DhanAdapter(
        DhanConfig.live(client_id="c", access_token="t"),
        http_transport=httpx.MockTransport(handler),
    )
    await a.connect()
    return a


def _now(minutes_after_open: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(T0 + minutes_after_open * 60, dt.timezone.utc)


async def test_intraday_payload_has_interval_and_timestamps() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(3), seen)
    await a.historical_bars(
        RELIANCE,
        timeframe="5m",
        start=dt.datetime(2024, 1, 2, 9, 15, tzinfo=IST),
        end=dt.datetime(2024, 1, 2, 15, 30, tzinfo=IST),
    )
    assert seen[-1].url.path.endswith("/charts/intraday")
    body = json.loads(seen[-1].content)
    assert body["interval"] == "5"
    assert body["securityId"] == "1333"
    assert body["exchangeSegment"] == "NSE_EQ"
    assert body["instrument"] == "EQUITY"
    assert body["fromDate"] == "2024-01-02 09:15:00"
    assert body["toDate"] == "2024-01-02 15:30:00"


async def test_utc_start_is_converted_to_ist_in_payload() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(1), seen)
    await a.historical_bars(
        RELIANCE,
        timeframe="1m",
        start=dt.datetime(2024, 1, 2, 3, 45, tzinfo=dt.timezone.utc),
        end=dt.datetime(2024, 1, 2, 10, 0, tzinfo=dt.timezone.utc),
    )
    body = json.loads(seen[-1].content)
    assert body["fromDate"] == "2024-01-02 09:15:00"
    assert body["toDate"] == "2024-01-02 15:30:00"


async def test_daily_uses_historical_endpoint_with_date_only() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(1, 86400), seen)
    await a.historical_bars(
        RELIANCE,
        timeframe="1d",
        start=dt.datetime(2024, 1, 1, tzinfo=IST),
        end=dt.datetime(2024, 1, 5, tzinfo=IST),
    )
    assert seen[-1].url.path.endswith("/charts/historical")
    body = json.loads(seen[-1].content)
    assert body["fromDate"] == "2024-01-01"
    assert "interval" not in body


async def test_unsupported_timeframe_raises_before_network() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(1), seen)
    n = len(seen)
    with pytest.raises(AdapterError):
        await a.historical_bars(
            RELIANCE,
            timeframe="7m",
            start=dt.datetime(2024, 1, 2, tzinfo=IST),
            end=dt.datetime(2024, 1, 3, tzinfo=IST),
        )
    assert len(seen) == n


async def test_forming_bar_is_dropped_and_order_ascending() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(3), seen)
    a._clock = lambda: _now(2.5)  # bars at +0,+1,+2 min; the +2 bar closes at +3
    bars = await a.historical_bars(RELIANCE, timeframe="1m", start=_now(0), end=_now(3))
    assert [b.ts for b in bars] == [T0 * 10**9, (T0 + 60) * 10**9]


async def test_all_closed_bars_kept() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(3), seen)
    a._clock = lambda: _now(3)
    bars = await a.historical_bars(RELIANCE, timeframe="1m", start=_now(0), end=_now(3))
    assert len(bars) == 3
