"""Market data methods for Angel One SmartAPI 2.0 adapter."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
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

from honba_adapters.angelone.constants import ANGELONE_TIMEFRAMES
from honba_adapters.angelone.parsing import parse_depth, parse_historical_bars, parse_quote

if TYPE_CHECKING:
    from honba_adapters.angelone.http import AngelOneHttpClient
    from honba_adapters.angelone.instruments import AngelOneInstrumentMaster
    from honba_adapters.angelone.websocket import AngelOneFeedClient

__all__ = ["AngelOneMarketDataMixin"]

_TIMEFRAME_MAP: dict[str, str] = {
    "1d": "ONE_DAY",
    "D": "ONE_DAY",
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "60m": "ONE_HOUR",
    "1h": "ONE_HOUR",
}


class AngelOneMarketDataMixin:
    """Implements MarketDataAdapter methods for AngelOneAdapter."""

    _http: AngelOneHttpClient
    _instruments: AngelOneInstrumentMaster
    _feed: AngelOneFeedClient | None
    _subscriptions: dict[str, tuple[Subscription, StreamCallback]]

    async def instruments(self) -> list[Instrument]:
        """The full instrument universe known to the adapter."""
        self.require_connected()  # type: ignore[attr-defined]
        return self._instruments.all_instruments()

    async def search_instruments(self, query: str) -> list[Instrument]:
        """Search instruments matching query symbol or company name."""
        self.require_connected()  # type: ignore[attr-defined]
        return self._instruments.search(query)

    async def quote(self, instrument_id: InstrumentId) -> QuoteTick:
        """Fetch current top-of-book market quote snapshot for an instrument."""
        self.require_connected()  # type: ignore[attr-defined]
        token = self._instruments.resolve_token(instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {instrument_id}")

        tradingsymbol, exchange = self._instruments.resolve_tradingsymbol_and_exchange(
            instrument_id
        )

        payload = {
            "mode": "FULL",
            "exchangeTokens": {exchange: [str(token)]},
            "tradingsymbol": tradingsymbol,
            "symboltoken": str(token),
            "exchange": exchange,
        }

        try:
            resp = await self._http.post(
                "/rest/secure/angelbroking/market/v1/quote",
                pool="data",
                json_body=payload,
            )
        except AdapterError as e:
            raise AdapterError(f"Failed to fetch quote for {instrument_id}: {e}") from e

        if not resp:
            raise AdapterError(f"unknown instrument {instrument_id}")

        # Unwrap response if nested
        data = resp
        if isinstance(resp, dict):
            if "fetched" in resp and isinstance(resp["fetched"], list) and resp["fetched"]:
                data = resp["fetched"][0]
            elif "data" in resp and isinstance(resp["data"], dict):
                data = resp["data"]

        return parse_quote(data, instrument_id)

    async def depth(self, instrument_id: InstrumentId, levels: int = 5) -> MarketDepth:
        """Fetch 5-level market depth snapshot for an instrument."""
        self.require_connected()  # type: ignore[attr-defined]
        token = self._instruments.resolve_token(instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {instrument_id}")

        tradingsymbol, exchange = self._instruments.resolve_tradingsymbol_and_exchange(
            instrument_id
        )

        payload = {
            "mode": "FULL",
            "exchangeTokens": {exchange: [str(token)]},
            "tradingsymbol": tradingsymbol,
            "symboltoken": str(token),
            "exchange": exchange,
        }

        try:
            resp = await self._http.post(
                "/rest/secure/angelbroking/market/v1/quote",
                pool="data",
                json_body=payload,
            )
        except AdapterError as e:
            raise AdapterError(f"Failed to fetch depth for {instrument_id}: {e}") from e

        if not resp:
            raise AdapterError(f"unknown instrument {instrument_id}")

        data = resp
        if isinstance(resp, dict):
            if "fetched" in resp and isinstance(resp["fetched"], list) and resp["fetched"]:
                data = resp["fetched"][0]
            elif "data" in resp and isinstance(resp["data"], dict):
                data = resp["data"]

        depth_obj = parse_depth(data, instrument_id)
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
        """Fetch historical bars in strictly ascending time order."""
        self.require_connected()  # type: ignore[attr-defined]
        token = self._instruments.resolve_token(instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {instrument_id}")

        _, exchange = self._instruments.resolve_tradingsymbol_and_exchange(instrument_id)
        interval = _TIMEFRAME_MAP.get(timeframe, ANGELONE_TIMEFRAMES.get(timeframe, "ONE_DAY"))

        payload = {
            "exchange": exchange,
            "symboltoken": str(token),
            "interval": interval,
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": end.strftime("%Y-%m-%d %H:%M"),
        }

        try:
            resp = await self._http.post(
                "/rest/secure/angelbroking/historical/v1/getCandleData",
                pool="historical",
                json_body=payload,
            )
        except AdapterError as e:
            raise AdapterError(f"historical bars failed for {instrument_id}: {e}") from e

        if not resp:
            return []

        return parse_historical_bars(resp, instrument_id)

    async def subscribe(
        self,
        instruments: Sequence[InstrumentId] | InstrumentId,
        *,
        mode: StreamMode,
        callback: StreamCallback,
    ) -> Subscription:
        """Start streaming live market data updates for instruments."""
        self.require_connected()  # type: ignore[attr-defined]
        inst_tuple = (instruments,) if isinstance(instruments, InstrumentId) else tuple(instruments)

        if not inst_tuple:
            raise ValueError("instruments must not be empty")

        for inst in inst_tuple:
            if self._instruments.resolve_token(inst) is None:
                raise AdapterError(f"unknown instrument {inst}")

        sub_id = f"angelone-sub-{uuid.uuid4().hex[:8]}"
        subscription = Subscription(id=sub_id, instruments=inst_tuple, mode=mode)

        if self._feed is not None:
            await self._feed.subscribe(inst_tuple, mode=mode, callback=callback)

        self._subscriptions[sub_id] = (subscription, callback)
        return subscription

    async def unsubscribe(self, subscription_id: str) -> None:
        """Stop an active subscription by subscription id."""
        self.require_connected()  # type: ignore[attr-defined]
        if subscription_id not in self._subscriptions:
            raise AdapterError(f"unknown subscription id '{subscription_id}'")

        sub, callback = self._subscriptions[subscription_id]
        if self._feed is not None:
            await self._feed.unsubscribe(sub.instruments, callback=callback)
        del self._subscriptions[subscription_id]
