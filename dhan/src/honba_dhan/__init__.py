"""DhanHQ broker adapter implementation for Honba (M2).

Adheres to ADR 0010: The Adapter Contract.
Implements the Adapter facade, MarketDataAdapter, and ExecutionAdapter role protocols.
"""

from __future__ import annotations

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

from honba_dhan.config import DhanConfig
from honba_dhan.data import DhanMarketDataMixin
from honba_dhan.execution import DhanExecutionMixin
from honba_dhan.http import DhanHttpClient
from honba_dhan.instruments import DhanInstrumentMaster
from honba_dhan.mock import make_dhan_mock_handler, make_dhan_mock_transport
from honba_dhan.websocket import DhanFeedClient

__all__ = ["DhanAdapter", "DhanConfig", "make_dhan_mock_handler", "make_dhan_mock_transport"]

_DHAN_CAPABILITIES = AdapterCapabilities(
    name="dhan",
    exchanges=frozenset({"NSE", "BSE", "MCX"}),
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


class DhanAdapter(DhanExecutionMixin, DhanMarketDataMixin, Adapter):
    """Facade for Dhan broker API integrating Execution and MarketData roles."""

    name: str = "dhan"

    def __init__(
        self,
        config: DhanConfig | None = None,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        instruments: DhanInstrumentMaster | None = None,
        **kwargs: Any,
    ) -> None:
        if config is None:
            kwargs.pop("name", None)
            mode = kwargs.pop("mode", None)
            if isinstance(mode, str):
                mode = RunMode.PAPER if mode.lower() == "paper" else RunMode.LIVE
            client_id = kwargs.pop("client_id", None)
            access_token = kwargs.pop("access_token", None)
            if mode == RunMode.PAPER or (client_id is None and access_token is None):
                config = DhanConfig.paper(
                    client_id=client_id or "PAPER_DHAN_USER",
                    access_token=access_token or "paper_token_valid_for_testing",
                    **kwargs,
                )
            else:
                config = DhanConfig.live(
                    client_id=str(client_id or ""),
                    access_token=str(access_token or ""),
                    **kwargs,
                )

        if http_transport is None and config.mode == RunMode.PAPER:
            http_transport = make_dhan_mock_transport()

        self._config = config
        self._http_transport = http_transport
        self._instruments = instruments or DhanInstrumentMaster(
            config.cache_dir / "scrip_master.db"
        )
        self._connected = False
        self._session_info: SessionInfo | None = None

        self._http = DhanHttpClient(config, transport=http_transport)
        self._feed: DhanFeedClient | None = None

        # State tracking for idempotency & orders
        self._orders_by_id = {}
        self._orders_by_client_id = {}
        self._subscriptions: dict[str, tuple[Subscription, StreamCallback]] = {}

    def capabilities(self) -> AdapterCapabilities:
        """The static capability descriptor for Dhan."""
        return _DHAN_CAPABILITIES

    def is_connected(self) -> bool:
        """Check if adapter is connected."""
        return self._connected

    async def session(self) -> SessionInfo:
        """Get the active session."""
        self.require_connected()
        assert self._session_info is not None
        return self._session_info

    async def connect(self) -> SessionInfo:
        """Authenticate and establish communication with Dhan."""
        if self._connected:
            raise AdapterError(f"adapter {self.name} is already connected")

        # In live mode without mock transport, validate token via fund limit fetch
        if self._config.mode == RunMode.LIVE and self._http_transport is None:
            await self._http.get("/fundlimit", pool="orders")

        self._connected = True
        self._session_info = SessionInfo(
            user_id=self._config.client_id,
            mode=self._config.mode,
            accounts=(self._config.client_id,),
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
