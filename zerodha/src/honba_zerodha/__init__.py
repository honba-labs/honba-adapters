"""Honba adapter for Zerodha Kite Connect v3.

Provides full lifecycle integration, order execution, market data, and KiteTicker streaming.
"""

from __future__ import annotations

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
from honba_zerodha.websocket import ZerodhaFeedClient

__all__ = ["ZerodhaAdapter", "ZerodhaConfig"]

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
        config: ZerodhaConfig,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        instruments: ZerodhaInstrumentMaster | None = None,
    ) -> None:
        self._config = config
        self._http_transport = http_transport
        self._instruments = instruments or ZerodhaInstrumentMaster(
            config.cache_dir / "instruments.csv"
        )
        self._connected = False
        self._session_info: SessionInfo | None = None

        self._http = ZerodhaHttpClient(config, transport=http_transport)
        self._feed: ZerodhaFeedClient | None = None

        # State tracking for idempotency & orders
        self._orders_by_id = {}
        self._orders_by_client_id = {}
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
