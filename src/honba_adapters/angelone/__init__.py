"""Honba adapter for Angel One SmartAPI 2.0.

Provides full ADR 0010 contract implementation: lifecycle, market data, execution,
headless RFC 6238 TOTP authentication, and SmartStream binary WebSocket decoding.
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

from honba_adapters.angelone.config import AngelOneConfig
from honba_adapters.angelone.constants import (
    ANGELONE_EXCHANGES,
    ANGELONE_ORDER_TYPE_WIRE,
    ANGELONE_PRODUCT_WIRE,
    ANGELONE_TIF_WIRE,
)
from honba_adapters.angelone.data import AngelOneMarketDataMixin
from honba_adapters.angelone.execution import AngelOneExecutionMixin
from honba_adapters.angelone.http import AngelOneHttpClient
from honba_adapters.angelone.instruments import AngelOneInstrumentMaster
from honba_adapters.angelone.mock import (
    make_angelone_mock_handler,
    make_angelone_mock_transport,
)
from honba_adapters.angelone.websocket import AngelOneFeedClient
from honba_adapters.common.maps import assert_covers

__all__ = [
    "AngelOneAdapter",
    "AngelOneConfig",
    "make_angelone_mock_handler",
    "make_angelone_mock_transport",
]

_ANGELONE_CAPABILITIES = AdapterCapabilities(
    name="angelone",
    exchanges=ANGELONE_EXCHANGES,
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

# Fail at import, not at order time, if a declared capability has no wire mapping.
assert_covers(
    _ANGELONE_CAPABILITIES,
    order_types=ANGELONE_ORDER_TYPE_WIRE,
    products=ANGELONE_PRODUCT_WIRE,
    tif=ANGELONE_TIF_WIRE,
)


class AngelOneAdapter(AngelOneExecutionMixin, AngelOneMarketDataMixin, Adapter):
    """Facade for Angel One SmartAPI 2.0 integrating Execution and MarketData roles."""

    name: str = "angelone"

    def __init__(
        self,
        config: AngelOneConfig | None = None,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        instruments: AngelOneInstrumentMaster | None = None,
        feed: AngelOneFeedClient | None = None,
        **kwargs: Any,
    ) -> None:
        if config is None:
            kwargs.pop("name", None)
            mode = kwargs.pop("mode", None)
            if isinstance(mode, str):
                mode = RunMode.PAPER if mode.lower() == "paper" else RunMode.LIVE
            api_key = kwargs.pop("api_key", None)
            client_code = kwargs.pop("client_code", None)
            jwt_token = kwargs.pop("jwt_token", None)
            if mode == RunMode.PAPER:
                config = AngelOneConfig.paper(
                    api_key=api_key or "mock_api_key",
                    client_code=client_code or "mock_client_code",
                    jwt_token=jwt_token or "mock_jwt_token",
                    **kwargs,
                )
            elif api_key is None and client_code is None:
                config = AngelOneConfig.from_env(**kwargs)
            else:
                config = AngelOneConfig.live(
                    api_key=str(api_key or ""),
                    client_code=str(client_code or ""),
                    jwt_token=str(jwt_token or ""),
                    **kwargs,
                )

        if http_transport is None and config.mode == RunMode.PAPER:
            http_transport = make_angelone_mock_transport()

        self._config = config
        self._http_transport = http_transport
        self._instruments = instruments or AngelOneInstrumentMaster(
            config.cache_dir / "scrip_master.json"
        )
        self._connected = False
        self._session_info: SessionInfo | None = None

        self._http = AngelOneHttpClient(config, transport=http_transport)
        self._feed: AngelOneFeedClient | None = feed

        # State tracking for idempotency & orders
        self._orders_by_id = {}
        self._orders_by_client_id = {}
        self._ambiguous: set[str] = set()
        self._subscriptions: dict[str, tuple[Subscription, StreamCallback]] = {}

    def capabilities(self) -> AdapterCapabilities:
        """The static capability descriptor for Angel One."""
        return _ANGELONE_CAPABILITIES

    def is_connected(self) -> bool:
        """Check if adapter is connected."""
        return self._connected

    async def session(self) -> SessionInfo:
        """Get the active session."""
        self.require_connected()
        assert self._session_info is not None
        return self._session_info

    async def connect(self) -> SessionInfo:
        """Authenticate and establish communication with Angel One."""
        if self._connected:
            raise AdapterError(f"adapter {self.name} is already connected")

        # In live mode without mock transport and without JWT token, run headless login
        if self._config.mode == RunMode.LIVE and self._http_transport is None:
            if not self._http.jwt_token and self._config.password:
                await self._http.login_by_password()
            elif self._http.jwt_token:
                await self._http.get("/rest/secure/angelbroking/user/v1/getProfile", pool="data")

        self._connected = True
        user = self._config.client_code or self._config.user_id or "angelone_user"
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
        self._subscriptions.clear()

        if self._feed is not None:
            await self._feed.stop()
            self._feed = None

        await self._http.close()
