"""Market data methods for DhanHQ adapter (M2).

Implements the MarketDataAdapter role protocol:
- Instrument master queries and symbol searches
- Top of book quotes and 5/20-level market depth snapshots
- Historical candles (minute and daily bars)
- Live WebSocket streaming subscriptions
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from honba.adapters.errors import AdapterError
from honba.adapters.models import (
    MarketDepth,
    StreamCallback,
    StreamMode,
    Subscription,
)
from honba.domain.bar import Bar
from honba.domain.instrument import Instrument, InstrumentId
from honba.domain.tick import QuoteTick

from honba_dhan.parsing import parse_depth, parse_historical_bars, parse_quote
from honba_dhan.timeframes import IST, timeframe_seconds, to_dhan_interval

if TYPE_CHECKING:
    from honba_dhan.http import DhanHttpClient
    from honba_dhan.instruments import DhanInstrumentMaster
    from honba_dhan.websocket import DhanFeedClient

__all__ = ["DhanMarketDataMixin"]


# Maximum span of one /charts/intraday request, in days. Source: Dhan docs
# (https://dhanhq.co/docs/v2/historical-data/): "only 90 days of data can be polled at once".
INTRADAY_MAX_DAYS = 90


def _windows(
    start: dt.datetime, end: dt.datetime, max_days: int
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Split ``[start, end]`` into consecutive windows of at most ``max_days`` days."""
    step = dt.timedelta(days=max_days)
    out: list[tuple[dt.datetime, dt.datetime]] = []
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    return out or [(start, end)]


def _to_ist(t: dt.datetime) -> dt.datetime:
    """Aware datetimes are converted to IST; naive ones are taken as IST already."""
    return t.replace(tzinfo=IST) if t.tzinfo is None else t.astimezone(IST)


class DhanMarketDataMixin:
    """Implements MarketDataAdapter methods for DhanAdapter."""

    _http: DhanHttpClient
    _instruments: DhanInstrumentMaster
    _feed: DhanFeedClient | None
    _subscriptions: dict[str, tuple[Subscription, StreamCallback]]
    _clock: Callable[[], dt.datetime]

    async def instruments(self) -> list[Instrument]:
        """The full instrument list known to the adapter."""
        self.require_connected()  # type: ignore[attr-defined]
        return self._instruments.all_instruments()

    async def search_instruments(self, query: str) -> list[Instrument]:
        """Search instruments matching query symbol or substring."""
        self.require_connected()  # type: ignore[attr-defined]
        return self._instruments.search_instruments(query)

    async def quote(self, instrument_id: InstrumentId) -> QuoteTick:
        """Fetch current top-of-book snapshot for an instrument."""
        self.require_connected()  # type: ignore[attr-defined]
        resolved = self._instruments.resolve_to_dhan(instrument_id)
        if resolved is None:
            raise AdapterError(f"unknown instrument {instrument_id}")
        sec_id, seg = resolved

        payload = {
            "securityId": sec_id,
            "exchangeSegment": seg,
            "instrument": "EQUITY",
        }
        try:
            resp = await self._http.post("/marketfeed/quote", pool="quote", json_body=payload)
        except AdapterError as e:
            if e.fatal:
                raise
            raise AdapterError(f"Failed to fetch quote for {instrument_id}: {e}") from e

        if not resp or (isinstance(resp, dict) and resp.get("status") == "error"):
            raise AdapterError(f"unknown instrument {instrument_id}")

        return parse_quote(resp if isinstance(resp, dict) else {}, instrument_id)

    async def depth(self, instrument_id: InstrumentId, levels: int = 5) -> MarketDepth:
        """Fetch market depth snapshot for an instrument."""
        self.require_connected()  # type: ignore[attr-defined]
        resolved = self._instruments.resolve_to_dhan(instrument_id)
        if resolved is None:
            raise AdapterError(f"unknown instrument {instrument_id}")
        sec_id, seg = resolved

        payload = {
            "securityId": sec_id,
            "exchangeSegment": seg,
            "instrument": "EQUITY",
        }
        try:
            resp = await self._http.post("/marketfeed/quote", pool="quote", json_body=payload)
        except AdapterError as e:
            if e.fatal:
                raise
            raise AdapterError(f"Failed to fetch depth for {instrument_id}: {e}") from e

        if not resp or (isinstance(resp, dict) and resp.get("status") == "error"):
            raise AdapterError(f"unknown instrument {instrument_id}")

        depth_obj = parse_depth(resp if isinstance(resp, dict) else {}, instrument_id)
        # Limit to requested levels
        if len(depth_obj.bids) > levels or len(depth_obj.asks) > levels:
            return MarketDepth(
                instrument_id=depth_obj.instrument_id,
                ts=depth_obj.ts,
                bids=depth_obj.bids[:levels],
                asks=depth_obj.asks[:levels],
            )
        return depth_obj

    async def historical_bars(
        self,
        instrument_id: InstrumentId,
        *,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[Bar]:
        """Fetch historical bars in ascending time order."""
        self.require_connected()  # type: ignore[attr-defined]
        resolved = self._instruments.resolve_to_dhan(instrument_id)
        if resolved is None:
            raise AdapterError(f"unknown instrument {instrument_id}")
        sec_id, seg = resolved

        try:
            span_s = timeframe_seconds(timeframe)
            daily = span_s >= 86400
            interval = None if daily else to_dhan_interval(timeframe)
        except ValueError as e:
            raise AdapterError(str(e)) from e

        base: dict[str, Any] = {
            "securityId": sec_id,
            "exchangeSegment": seg,
            "instrument": "INDEX" if seg == "IDX_I" else "EQUITY",
            "oi": False,
        }
        if daily:
            windows = [(_to_ist(start), _to_ist(end))]
            endpoint = "/charts/historical"
            fmt = "%Y-%m-%d"
        else:
            base["interval"] = interval
            windows = _windows(_to_ist(start), _to_ist(end), INTRADAY_MAX_DAYS)
            endpoint = "/charts/intraday"
            fmt = "%Y-%m-%d %H:%M:%S"

        by_ts: dict[int, Bar] = {}
        for w_start, w_end in windows:
            payload = {**base, "fromDate": w_start.strftime(fmt), "toDate": w_end.strftime(fmt)}
            if daily:
                payload["expiryCode"] = 0
            try:
                # Each call acquires a token from the shared "data" rate-limit pool (5/s).
                resp = await self._http.post(endpoint, pool="data", json_body=payload)
            except AdapterError as e:
                if e.fatal:
                    raise  # auth / data-plan errors keep their clear message and fatal type
                raise AdapterError(f"historical bars failed for {instrument_id}: {e}") from e

            if not resp or (isinstance(resp, dict) and resp.get("status") == "error"):
                continue
            for b in parse_historical_bars(
                resp if isinstance(resp, dict) else {}, instrument_id, timeframe
            ):
                by_ts[b.ts] = b  # windows share an endpoint instant: later window wins
        bars = [by_ts[k] for k in sorted(by_ts)]
        # ``Bar.ts`` is the bar START; drop the still-forming bar (closed bars only).
        now_ns = int(self._clock().timestamp() * 1_000_000_000)
        return [b for b in bars if b.ts + span_s * 1_000_000_000 <= now_ns]

    async def subscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        mode: StreamMode,
        callback: StreamCallback,
    ) -> Subscription:
        """Start streaming live market data updates."""
        self.require_connected()  # type: ignore[attr-defined]
        if not instruments:
            raise ValueError("instruments must not be empty")

        sub_id = f"dhan-sub-{uuid.uuid4().hex[:8]}"
        subscription = Subscription(id=sub_id, instruments=instruments, mode=mode)
        self._subscriptions[sub_id] = (subscription, callback)

        if self._feed is not None:
            await self._feed.subscribe(instruments, mode=mode, callback=callback)

        return subscription

    async def unsubscribe(self, subscription_id: str) -> None:
        """Stop a live subscription."""
        self.require_connected()  # type: ignore[attr-defined]
        if subscription_id not in self._subscriptions:
            raise AdapterError(f"unknown subscription id '{subscription_id}'")

        sub, callback = self._subscriptions.pop(subscription_id)
        if self._feed is not None:
            await self._feed.unsubscribe(sub.instruments, callback=callback)
