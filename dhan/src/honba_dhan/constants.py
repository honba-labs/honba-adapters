"""Constants, enums, and mapping tables for the DhanHQ adapter (M2)."""

from __future__ import annotations

from typing import Final

from honba.adapters.models import Product
from honba.domain.order import OrderSide, OrderStatus, OrderType

__all__ = [
    "DEFAULT_DATA_URL",
    "DEFAULT_FEED_URL",
    "DEFAULT_REST_URL",
    "DHAN_FEED_CODE_DEPTH_20",
    "DHAN_FEED_CODE_LTP",
    "DHAN_FEED_CODE_QUOTE",
    "DHAN_ORDER_TYPE_TO_HONBA",
    "DHAN_PRODUCT_TO_HONBA",
    "DHAN_SIDE_TO_HONBA",
    "DHAN_STATUS_TO_HONBA",
    "EXCHANGE_TO_SEGMENT",
    "HONBA_ORDER_TYPE_TO_DHAN",
    "HONBA_PRODUCT_TO_DHAN",
    "HONBA_SIDE_TO_DHAN",
    "SEGMENT_TO_EXCHANGE",
]

DEFAULT_REST_URL: Final[str] = "https://api.dhan.co/v2"
DEFAULT_FEED_URL: Final[str] = "wss://api-feed.dhan.co"
DEFAULT_DATA_URL: Final[str] = "https://images.dhan.co/api-data"

# WebSocket Feed Message Codes
DHAN_FEED_CODE_LTP: Final[int] = 17
DHAN_FEED_CODE_QUOTE: Final[int] = 18
DHAN_FEED_CODE_DEPTH_20: Final[int] = 19

# Product Mapping
HONBA_PRODUCT_TO_DHAN: Final[dict[Product, str]] = {
    Product.DELIVERY: "CNC",
    Product.INTRADAY: "INTRADAY",
    Product.CARRY: "MARGIN",
}

DHAN_PRODUCT_TO_HONBA: Final[dict[str, Product]] = {
    "CNC": Product.DELIVERY,
    "INTRADAY": Product.INTRADAY,
    "MARGIN": Product.CARRY,
    "MTF": Product.CARRY,
    "CO": Product.INTRADAY,
    "BO": Product.INTRADAY,
}

# Order Type Mapping
HONBA_ORDER_TYPE_TO_DHAN: Final[dict[OrderType, str]] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LIMIT: "STOP_LOSS",
    OrderType.STOP_MARKET: "STOP_LOSS_MARKET",
}

DHAN_ORDER_TYPE_TO_HONBA: Final[dict[str, OrderType]] = {
    "MARKET": OrderType.MARKET,
    "LIMIT": OrderType.LIMIT,
    "STOP_LOSS": OrderType.STOP_LIMIT,
    "STOP_LOSS_MARKET": OrderType.STOP_MARKET,
}

# Order Side Mapping
HONBA_SIDE_TO_DHAN: Final[dict[OrderSide, str]] = {
    OrderSide.BUY: "BUY",
    OrderSide.SELL: "SELL",
}

DHAN_SIDE_TO_HONBA: Final[dict[str, OrderSide]] = {
    "BUY": OrderSide.BUY,
    "SELL": OrderSide.SELL,
}

# Order Status Mapping
DHAN_STATUS_TO_HONBA: Final[dict[str, OrderStatus]] = {
    "TRANSIT": OrderStatus.SUBMITTED,
    "PENDING": OrderStatus.ACCEPTED,
    "TRADED": OrderStatus.FILLED,
    "PART_TRADED": OrderStatus.PARTIALLY_FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}

# Exchange / Segment Mapping
EXCHANGE_TO_SEGMENT: Final[dict[tuple[str, str], str]] = {
    ("NSE", "EQUITY"): "NSE_EQ",
    ("BSE", "EQUITY"): "BSE_EQ",
    ("NSE", "FNO"): "NSE_FNO",
    ("BSE", "FNO"): "BSE_FNO",
    ("MCX", "COMMODITY"): "MCX_COMM",
}

SEGMENT_TO_EXCHANGE: Final[dict[str, tuple[str, str]]] = {
    "NSE_EQ": ("NSE", "EQUITY"),
    "BSE_EQ": ("BSE", "EQUITY"),
    "NSE_FNO": ("NSE", "FNO"),
    "BSE_FNO": ("BSE", "FNO"),
    "MCX_COMM": ("MCX", "COMMODITY"),
}
