"""Constants and protocol wire-mappings for Zerodha Kite Connect v3."""

from __future__ import annotations

from honba.adapters.models import Product, StreamMode
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce
from honba_adapters_shared.maps import WireMap

ZERODHA_DEFAULT_HTTP_ENDPOINT: str = "https://api.kite.trade"
ZERODHA_DEFAULT_WS_ENDPOINT: str = "wss://ws.kite.trade"

# Wire mappings: one forward dict each; the reverse table is derived (see WireMap).
ZERODHA_PRODUCT_WIRE: WireMap[Product] = WireMap(
    {Product.DELIVERY: "CNC", Product.INTRADAY: "MIS", Product.CARRY: "NRML"},
    name="zerodha product",
)

ZERODHA_ORDER_TYPE_WIRE: WireMap[OrderType] = WireMap(
    {
        OrderType.MARKET: "MARKET",
        OrderType.LIMIT: "LIMIT",
        OrderType.STOP_LIMIT: "SL",
        OrderType.STOP_MARKET: "SL-M",
    },
    name="zerodha order type",
)

# Kite validity: only DAY and IOC exist for regular orders.
ZERODHA_TIF_WIRE: WireMap[TimeInForce] = WireMap(
    {TimeInForce.DAY: "DAY", TimeInForce.IOC: "IOC"}, name="zerodha time in force"
)

ZERODHA_SIDE_WIRE: WireMap[OrderSide] = WireMap(
    {OrderSide.BUY: "BUY", OrderSide.SELL: "SELL"}, name="zerodha side"
)

# Public names kept for existing importers; aliases of the maps above.
ZERODHA_PRODUCT_MAP: dict[Product, str] = ZERODHA_PRODUCT_WIRE.forward
ZERODHA_REVERSE_PRODUCT_MAP: dict[str, Product] = ZERODHA_PRODUCT_WIRE.reverse
ZERODHA_ORDER_TYPE_MAP: dict[OrderType, str] = ZERODHA_ORDER_TYPE_WIRE.forward
ZERODHA_REVERSE_ORDER_TYPE_MAP: dict[str, OrderType] = ZERODHA_ORDER_TYPE_WIRE.reverse
ZERODHA_TIF_MAP: dict[TimeInForce, str] = ZERODHA_TIF_WIRE.forward
ZERODHA_SIDE_MAP: dict[OrderSide, str] = ZERODHA_SIDE_WIRE.forward
ZERODHA_REVERSE_SIDE_MAP: dict[str, OrderSide] = ZERODHA_SIDE_WIRE.reverse

ZERODHA_STATUS_MAP: dict[str, OrderStatus] = {
    "COMPLETE": OrderStatus.FILLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
    "OPEN": OrderStatus.ACCEPTED,
    "TRIGGER PENDING": OrderStatus.ACCEPTED,
    "PUT ORDER REQ RECEIVED": OrderStatus.SUBMITTED,
    "VALIDATION PENDING": OrderStatus.SUBMITTED,
    "MODIFY PENDING": OrderStatus.ACCEPTED,
}

# KiteTicker feed modes
ZERODHA_FEED_MODE_LTP: str = "ltp"
ZERODHA_FEED_MODE_QUOTE: str = "quote"
ZERODHA_FEED_MODE_FULL: str = "full"

ZERODHA_STREAM_MODE_MAP: dict[StreamMode, str] = {
    StreamMode.LTP: ZERODHA_FEED_MODE_LTP,
    StreamMode.QUOTE: ZERODHA_FEED_MODE_QUOTE,
    StreamMode.DEPTH: ZERODHA_FEED_MODE_FULL,
}
