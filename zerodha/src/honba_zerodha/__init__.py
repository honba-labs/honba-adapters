"""Honba adapter for Zerodha Kite Connect v3.

Provides full lifecycle integration, order execution, market data, and KiteTicker streaming.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx
from honba.adapters.base import Adapter
from honba.adapters.capabilities import AdapterCapabilities, Capability
from honba.adapters.errors import AdapterError
from honba.adapters.models import (
    Product,
    RunMode,
    SessionInfo,
    StreamCallback,
    StreamMode,
    Subscription,
)
from honba.domain.order import OrderType, TimeInForce

from honba_zerodha.config import ZerodhaConfig
from honba_zerodha.data import ZerodhaMarketDataMixin
from honba_zerodha.execution import ZerodhaExecutionMixin
from honba_zerodha.http import ZerodhaHttpClient
from honba_zerodha.instruments import ZerodhaInstrumentMaster
from honba_zerodha.mock import make_zerodha_mock_handler, make_zerodha_mock_transport
from honba_zerodha.websocket import ZerodhaFeedClient

__all__ = [
    "ZerodhaAdapter",
    "ZerodhaConfig",
    "make_zerodha_mock_handler",
    "make_zerodha_mock_transport",
]

_ZERODHA_CAPABILITIES = AdapterCapabilities(
    name="zerodha",
    exchanges=frozenset({"NSE", "BSE", "NFO", "BFO", "MCX", "CDS"}),
    features=frozenset(
        {
            Capability.PLACE_ORDER,
            Capability.MODIFY_ORDER,
            Capability.CANCEL_ORDER,
            Capability.CANCEL_ALL,
            Capability.ORDER_BOOK,
            Capability.TRADE_BOOK,
            Capability.POSITIONS,
            Capability.HOLDINGS,
            Capability.FUNDS,
            Capability.MARGIN,
            Capability.QUOTES,
            Capability.DEPTH,
            Capability.HISTORICAL_BARS,
            Capability.INSTRUMENT_MASTER,
        }
    ),
    products=frozenset({Product.DELIVERY, Product.INTRADAY, Product.CARRY}),
    order_types=frozenset(
        {
            OrderType.MARKET,
            OrderType.LIMIT,
            OrderType.STOP_LIMIT,
            OrderType.STOP_MARKET,
        }
    ),
    time_in_force=frozenset({TimeInForce.DAY, TimeInForce.IOC}),
    stream_modes=frozenset({StreamMode.LTP, StreamMode.QUOTE, StreamMode.DEPTH}),
)


class ZerodhaAdapter(ZerodhaExecutionMixin, ZerodhaMarketDataMixin, Adapter):
    """Facade for Zerodha broker API integrating Execution and MarketData roles."""

    name: str = "zerodha"

    def __init__(
        self,
        config: ZerodhaConfig | None = None,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        instruments: ZerodhaInstrumentMaster | None = None,
        feed: ZerodhaFeedClient | None = None,
        **kwargs: Any,
    ) -> None:
        if config is None:
            kwargs.pop("name", None)
            mode = kwargs.pop("mode", None)
            if isinstance(mode, str):
                mode = RunMode.PAPER if mode.lower() == "paper" else RunMode.LIVE
            api_key = kwargs.pop("api_key", None)
            access_token = kwargs.pop("access_token", None)
            if mode == RunMode.PAPER or (api_key is None and access_token is None):
                config = ZerodhaConfig.paper(
                    api_key=api_key or "mock_api_key",
                    access_token=access_token or "mock_access_token",
                    **kwargs,
                )
            else:
                config = ZerodhaConfig.live(
                    api_key=str(api_key or ""),
                    access_token=str(access_token or ""),
                    **kwargs,
                )

        if http_transport is None and config.mode == RunMode.PAPER:
            http_transport = make_zerodha_mock_transport()

        self._config = config
        self._http_transport = http_transport
        self._instruments = instruments or ZerodhaInstrumentMaster(
            config.cache_dir / "instruments.csv"
        )
        self._connected = False
        self._session_info: SessionInfo | None = None

        self._http = ZerodhaHttpClient(config, transport=http_transport)
        self._feed: ZerodhaFeedClient | None = feed

        # State tracking for idempotency & orders
        self._orders_by_id = {}
        self._orders_by_client_id = {}
        self._ambiguous: set[str] = set()
        self._emitted_trade_ids: set[str] = set()
        self._subscriptions: dict[str, tuple[Subscription, StreamCallback]] = {}

    def capabilities(self) -> AdapterCapabilities:
        """The static capability descriptor for Zerodha."""
        return _ZERODHA_CAPABILITIES

    def is_connected(self) -> bool:
        """Check if adapter is connected."""
        return self._connected

    async def session(self) -> SessionInfo:
        """Get the active session."""
        self.require_connected()
        assert self._session_info is not None
        return self._session_info

    async def connect(self) -> SessionInfo:
        """Authenticate and establish communication with Zerodha Kite Connect."""
        if self._connected:
            raise AdapterError(f"adapter {self.name} is already connected")

        # In live mode without mock transport, validate token via user margins fetch
        if self._config.mode == RunMode.LIVE and self._http_transport is None:
            await self._http.get("/user/margins", pool="orders")

        self._connected = True
        user = self._config.user_id or "zerodha_user"
        self._session_info = SessionInfo(
            user_id=user,
            mode=self._config.mode,
            accounts=(user,),
        )
        return self._session_info

    async def generate_session(self, request_token: str, api_secret: str | None = None) -> str:
        """Exchange a login ``request_token`` for an access token and return the Kite user id.

        ``api_secret`` defaults to the configured one. The secret is only used to compute the
        request checksum and is never sent or logged.
        """
        secret = api_secret or self._config.api_secret
        if not secret:
            raise AdapterError("an api secret is required to generate a session")
        user_id = await self._http.generate_session(request_token, secret)
        token = self._http.access_token or ""
        self._config = replace(self._config, access_token=token, user_id=user_id)
        if self._feed is not None:  # its URL carries the old token; rebuilt on next subscribe
            await self._feed.stop()
            self._feed = None
        return user_id

    async def refresh_instruments(self, exchange: str | None = None) -> int:
        """Download Kite's daily instruments dump and load it into the token map.

        Returns the number of instruments loaded. ``exchange`` limits the dump to one exchange.
        """
        path = f"/instruments/{exchange}" if exchange else "/instruments"
        text = await self._http.get(path, pool="data")
        if not isinstance(text, str):
            raise AdapterError("Zerodha instruments dump was not CSV")
        return self._instruments.load_from_csv_text(text)

    async def _ensure_feed(self) -> ZerodhaFeedClient | None:
        """The ticker client; created and started on first use for live, networked adapters."""
        if (
            self._feed is None
            and self._config.mode == RunMode.LIVE
            and self._http_transport is None
        ):
            self._feed = ZerodhaFeedClient(self._config, self._instruments)
            await self._feed.start()
        return self._feed

    async def disconnect(self) -> None:
        """Release all connections and transports."""
        if not self._connected:
            raise AdapterError(f"adapter {self.name} is not connected")

        self._connected = False
        self._session_info = None

        if self._feed is not None:
            await self._feed.stop()
            self._feed = None

        await self._http.close()
