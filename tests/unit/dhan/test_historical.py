"""historical_bars: request payload shape and closed-bar filtering (fake HTTP only)."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
from honba.adapters.errors import AdapterError
from honba.domain.instrument import InstrumentId

from honba_adapters.dhan import DhanAdapter, DhanConfig

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


# ---------------------------------------------------------------- chunking


def _ist(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)


async def test_intraday_range_is_chunked_merged_sorted_deduped() -> None:
    from honba_adapters.dhan.data import INTRADAY_MAX_DAYS

    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((body["fromDate"], body["toDate"]))
        lo, hi = _ist(body["fromDate"]), _ist(body["toDate"])
        # one bar at each window edge (so the shared boundary bar is returned twice) + the
        # series deliberately descending to prove the adapter sorts.
        stamps = sorted({int(lo.timestamp()), int(hi.timestamp())}, reverse=True)
        return httpx.Response(
            200,
            json={
                "open": [1.0] * len(stamps),
                "high": [2.0] * len(stamps),
                "low": [0.5] * len(stamps),
                "close": [1.5] * len(stamps),
                "volume": [10.0] * len(stamps),
                "timestamp": [float(t) for t in stamps],
            },
        )

    a = DhanAdapter(
        DhanConfig.live(client_id="c", access_token="t"),
        http_transport=httpx.MockTransport(handler),
    )
    a._clock = lambda: dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
    await a.connect()
    start = dt.datetime(2024, 1, 1, 9, 15, tzinfo=IST)
    end = start + dt.timedelta(days=2 * INTRADAY_MAX_DAYS + 10)
    bars = await a.historical_bars(RELIANCE, timeframe="5m", start=start, end=end)

    w1 = start + dt.timedelta(days=INTRADAY_MAX_DAYS)
    w2 = start + dt.timedelta(days=2 * INTRADAY_MAX_DAYS)
    fmt = "%Y-%m-%d %H:%M:%S"
    assert seen == [
        (start.strftime(fmt), w1.strftime(fmt)),
        (w1.strftime(fmt), w2.strftime(fmt)),
        (w2.strftime(fmt), end.strftime(fmt)),
    ]
    ts = [b.ts for b in bars]
    assert ts == sorted(ts)
    assert len(ts) == len(set(ts)) == 4  # start, w1 (dup), w2 (dup), end
    assert ts[0] == int(start.timestamp()) * 10**9


async def test_short_range_is_one_request_and_each_window_hits_the_data_limiter() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(2), seen)
    acquired: list[str] = []
    real = a._http.limiter.acquire

    async def spy(pool: str, tokens: int = 1) -> None:
        acquired.append(pool)
        await real(pool, tokens)

    a._http.limiter.acquire = spy  # type: ignore[method-assign]
    await a.historical_bars(
        RELIANCE,
        timeframe="1m",
        start=dt.datetime(2024, 1, 2, 9, 15, tzinfo=IST),
        end=dt.datetime(2024, 1, 2, 15, 30, tzinfo=IST),
    )
    assert len(seen) == 1 and acquired == ["data"]


async def test_closed_bars_only_applies_across_windows() -> None:
    seen: list[httpx.Request] = []
    a = await _adapter(_resp(3), seen)
    a._clock = lambda: _now(1.5)  # type: ignore[attr-defined]
    bars = await a.historical_bars(
        RELIANCE,
        timeframe="1m",
        start=dt.datetime(2024, 1, 2, 9, 15, tzinfo=IST),
        end=dt.datetime(2024, 1, 2, 15, 30, tzinfo=IST),
    )
    assert len(bars) == 1
