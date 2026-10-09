"""Constants and protocol wire-mappings for Zerodha Kite Connect v3."""

from __future__ import annotations

from honba.adapters.models import Product, StreamMode
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce

ZERODHA_DEFAULT_HTTP_ENDPOINT: str = "https://api.kite.trade"
ZERODHA_DEFAULT_WS_ENDPOINT: str = "wss://ws.kite.trade"

ZERODHA_PRODUCT_MAP: dict[Product, str] = {
    Product.DELIVERY: "CNC",
    Product.INTRADAY: "MIS",
    Product.CARRY: "NRML",
}

ZERODHA_REVERSE_PRODUCT_MAP: dict[str, Product] = {
    "CNC": Product.DELIVERY,
    "MIS": Product.INTRADAY,
    "NRML": Product.CARRY,
}

ZERODHA_ORDER_TYPE_MAP: dict[OrderType, str] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LIMIT: "SL",
    OrderType.STOP_MARKET: "SL-M",
}

ZERODHA_REVERSE_ORDER_TYPE_MAP: dict[str, OrderType] = {
    "MARKET": OrderType.MARKET,
    "LIMIT": OrderType.LIMIT,
    "SL": OrderType.STOP_LIMIT,
    "SL-M": OrderType.STOP_MARKET,
}

# Kite validity: only DAY and IOC exist for regular orders.
ZERODHA_TIF_MAP: dict[TimeInForce, str] = {
    TimeInForce.DAY: "DAY",
    TimeInForce.IOC: "IOC",
}

ZERODHA_SIDE_MAP: dict[OrderSide, str] = {
    OrderSide.BUY: "BUY",
    OrderSide.SELL: "SELL",
}

ZERODHA_REVERSE_SIDE_MAP: dict[str, OrderSide] = {
    "BUY": OrderSide.BUY,
    "SELL": OrderSide.SELL,
}

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
