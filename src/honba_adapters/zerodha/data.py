"""Market data methods for Zerodha Kite Connect adapter (M3)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

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

from honba_adapters.zerodha.parsing import parse_depth, parse_historical_candles, parse_quote

if TYPE_CHECKING:
    from honba_adapters.zerodha.http import ZerodhaHttpClient
    from honba_adapters.zerodha.instruments import ZerodhaInstrumentMaster
    from honba_adapters.zerodha.websocket import ZerodhaFeedClient

__all__ = ["ZerodhaMarketDataMixin"]

_TIMEFRAME_MAP: dict[str, str] = {
    "1d": "day",
    "D": "day",
    "1m": "minute",
    "3m": "3minute",
    "5m": "5minute",
    "10m": "10minute",
    "15m": "15minute",
    "30m": "30minute",
    "60m": "60minute",
}


class ZerodhaMarketDataMixin:
    """Implements MarketDataAdapter methods for ZerodhaAdapter."""

    _http: ZerodhaHttpClient
    _instruments: ZerodhaInstrumentMaster
    _feed: ZerodhaFeedClient | None
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
        token = self._instruments.resolve_token(instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {instrument_id}")

        query_key = f"{instrument_id.exchange}:{instrument_id.symbol}"
        try:
            resp = await self._http.get("/quote", pool="data", params={"i": query_key})
        except AdapterError as e:
            raise AdapterError(f"Failed to fetch quote for {instrument_id}: {e}") from e

        if not resp:
            raise AdapterError(f"unknown instrument {instrument_id}")

        quote_data = (
            resp.get(query_key)
            or resp.get(str(token))
            or resp.get("data", {}).get(query_key)
            or resp
        )
        if not isinstance(quote_data, dict):
            raise AdapterError(f"unknown instrument {instrument_id}")

        return parse_quote(quote_data, instrument_id)

    async def depth(self, instrument_id: InstrumentId, levels: int = 5) -> MarketDepth:
        """Fetch market depth snapshot for an instrument."""
        self.require_connected()  # type: ignore[attr-defined]
        token = self._instruments.resolve_token(instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {instrument_id}")

        query_key = f"{instrument_id.exchange}:{instrument_id.symbol}"
        try:
            resp = await self._http.get("/quote", pool="data", params={"i": query_key})
        except AdapterError as e:
            raise AdapterError(f"Failed to fetch depth for {instrument_id}: {e}") from e

        if not resp:
            raise AdapterError(f"unknown instrument {instrument_id}")

        quote_data = (
            resp.get(query_key)
            or resp.get(str(token))
            or resp.get("data", {}).get(query_key)
            or resp
        )
        if not isinstance(quote_data, dict):
            raise AdapterError(f"unknown instrument {instrument_id}")

        depth_obj = parse_depth(quote_data, instrument_id)
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
        token = self._instruments.resolve_token(instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {instrument_id}")

        interval = _TIMEFRAME_MAP.get(timeframe, timeframe)
        params = {
            "from": start.strftime("%Y-%m-%d %H:%M:%S"),
            "to": end.strftime("%Y-%m-%d %H:%M:%S"),
        }

        try:
            resp = await self._http.get(
                f"/instruments/historical/{token}/{interval}",
                pool="historical",
                params=params,
            )
        except AdapterError as e:
            raise AdapterError(f"historical bars failed for {instrument_id}: {e}") from e

        if not resp:
            return []

        return parse_historical_candles(resp, instrument_id, timeframe)

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

        for inst in instruments:
            if self._instruments.resolve_token(inst) is None:
                raise AdapterError(f"unknown instrument {inst}")

        sub_id = f"kite-sub-{uuid.uuid4().hex[:8]}"
        subscription = Subscription(id=sub_id, instruments=instruments, mode=mode)

        # Tell the ticker first: a failure leaves no stale subscription behind.
        feed = await self._ensure_feed()  # type: ignore[attr-defined]
        if feed is not None:
            await feed.subscribe(instruments, mode=mode, callback=callback)
        self._subscriptions[sub_id] = (subscription, callback)

        return subscription

    async def unsubscribe(self, subscription_id: str) -> None:
        """Stop a live subscription."""
        self.require_connected()  # type: ignore[attr-defined]
        if subscription_id not in self._subscriptions:
            raise AdapterError(f"unknown subscription id '{subscription_id}'")

        sub, callback = self._subscriptions[subscription_id]
        if self._feed is not None:
            await self._feed.unsubscribe(sub.instruments, callback=callback)
        del self._subscriptions[subscription_id]
