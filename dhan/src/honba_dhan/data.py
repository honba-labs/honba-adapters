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

if TYPE_CHECKING:
    from honba_dhan.http import DhanHttpClient
    from honba_dhan.instruments import DhanInstrumentMaster
    from honba_dhan.websocket import DhanFeedClient

__all__ = ["DhanMarketDataMixin"]


class DhanMarketDataMixin:
    """Implements MarketDataAdapter methods for DhanAdapter."""

    _http: DhanHttpClient
    _instruments: DhanInstrumentMaster
    _feed: DhanFeedClient | None
    _subscriptions: dict[str, tuple[Subscription, StreamCallback]]

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
            resp = await self._http.post("/marketfeed/quote", pool="data", json_body=payload)
        except AdapterError as e:
            # Fallback or wrap error
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
            resp = await self._http.post("/marketfeed/quote", pool="data", json_body=payload)
        except AdapterError as e:
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

        payload: dict[str, Any] = {
            "securityId": sec_id,
            "exchangeSegment": seg,
            "instrument": "EQUITY",
            "fromDate": start.strftime("%Y-%m-%d"),
            "toDate": end.strftime("%Y-%m-%d"),
        }
        endpoint = "/charts/historical" if timeframe == "1d" else "/charts/intraday"

        try:
            resp = await self._http.post(endpoint, pool="data", json_body=payload)
        except AdapterError as e:
            raise AdapterError(f"historical bars failed for {instrument_id}: {e}") from e

        if not resp or (isinstance(resp, dict) and resp.get("status") == "error"):
            return []

        return parse_historical_bars(
            resp if isinstance(resp, dict) else {}, instrument_id, timeframe
        )

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
