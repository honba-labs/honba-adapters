"""Constants, enums, and mapping tables for the DhanHQ adapter (M2)."""

from __future__ import annotations

from typing import Final

from honba.adapters.models import Product
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce
from honba_adapters_shared.maps import WireMap

__all__ = [
    "DEFAULT_DATA_URL",
    "DEFAULT_FEED_URL",
    "DEFAULT_REST_URL",
    "DHAN_FEED_CODE_DEPTH_20",
    "DHAN_FEED_CODE_LTP",
    "DHAN_FEED_CODE_QUOTE",
    "DHAN_FEED_V2_DISCONNECT",
    "DHAN_FEED_V2_FULL",
    "DHAN_FEED_V2_QUOTE",
    "DHAN_FEED_V2_REQUEST",
    "DHAN_FEED_V2_SEGMENTS",
    "DHAN_FEED_V2_TICKER",
    "DHAN_ORDER_TYPE_TO_HONBA",
    "DHAN_ORDER_TYPE_WIRE",
    "DHAN_PRODUCT_TO_HONBA",
    "DHAN_PRODUCT_WIRE",
    "DHAN_SIDE_TO_HONBA",
    "DHAN_SIDE_WIRE",
    "DHAN_STATUS_TO_HONBA",
    "DHAN_TIF_WIRE",
    "EXCHANGE_TO_SEGMENT",
    "HONBA_ORDER_TYPE_TO_DHAN",
    "HONBA_PRODUCT_TO_DHAN",
    "HONBA_SIDE_TO_DHAN",
    "HONBA_TIF_TO_DHAN",
    "SEGMENT_TO_EXCHANGE",
]

DEFAULT_REST_URL: Final[str] = "https://api.dhan.co/v2"
DEFAULT_FEED_URL: Final[str] = "wss://api-feed.dhan.co"
DEFAULT_DATA_URL: Final[str] = "https://images.dhan.co/api-data"

# WebSocket Feed v2 (little-endian binary, auth via URL query) response codes
DHAN_FEED_V2_TICKER: Final[int] = 2
DHAN_FEED_V2_QUOTE: Final[int] = 4
DHAN_FEED_V2_FULL: Final[int] = 8
DHAN_FEED_V2_DISCONNECT: Final[int] = 50

# v2 request codes (subscribe, unsubscribe) per feed mode
DHAN_FEED_V2_REQUEST: Final[dict[str, tuple[int, int]]] = {
    "ltp": (15, 16),
    "quote": (17, 18),
    "depth": (21, 22),
}

# v2 header segment byte -> exchange segment name
DHAN_FEED_V2_SEGMENTS: Final[dict[int, str]] = {
    0: "IDX_I",
    1: "NSE_EQ",
    2: "NSE_FNO",
    3: "NSE_CURRENCY",
    4: "BSE_EQ",
    5: "MCX_COMM",
    7: "BSE_CURRENCY",
    8: "BSE_FNO",
}

# WebSocket Feed Message Codes (legacy, pre-v2 layout kept for the existing decoders)
DHAN_FEED_CODE_LTP: Final[int] = 17
DHAN_FEED_CODE_QUOTE: Final[int] = 18
DHAN_FEED_CODE_DEPTH_20: Final[int] = 19

# Wire mappings: one forward dict each; the reverse table is derived (see WireMap).
DHAN_PRODUCT_WIRE: Final[WireMap[Product]] = WireMap(
    {
        Product.DELIVERY: "CNC",
        Product.INTRADAY: "INTRADAY",
        Product.CARRY: "MARGIN",
    },
    # Returned by Dhan but never sent.
    extra_inbound={"MTF": Product.CARRY, "CO": Product.INTRADAY, "BO": Product.INTRADAY},
    name="dhan product",
)

DHAN_ORDER_TYPE_WIRE: Final[WireMap[OrderType]] = WireMap(
    {
        OrderType.MARKET: "MARKET",
        OrderType.LIMIT: "LIMIT",
        OrderType.STOP_LIMIT: "STOP_LOSS",
        OrderType.STOP_MARKET: "STOP_LOSS_MARKET",
    },
    name="dhan order type",
)

DHAN_SIDE_WIRE: Final[WireMap[OrderSide]] = WireMap(
    {OrderSide.BUY: "BUY", OrderSide.SELL: "SELL"}, name="dhan side"
)

# Dhan order validity: DAY and IOC.
DHAN_TIF_WIRE: Final[WireMap[TimeInForce]] = WireMap(
    {TimeInForce.DAY: "DAY", TimeInForce.IOC: "IOC"}, name="dhan time in force"
)

# Public names kept for existing importers; aliases of the maps above.
HONBA_PRODUCT_TO_DHAN: Final[dict[Product, str]] = DHAN_PRODUCT_WIRE.forward
DHAN_PRODUCT_TO_HONBA: Final[dict[str, Product]] = DHAN_PRODUCT_WIRE.reverse
HONBA_ORDER_TYPE_TO_DHAN: Final[dict[OrderType, str]] = DHAN_ORDER_TYPE_WIRE.forward
DHAN_ORDER_TYPE_TO_HONBA: Final[dict[str, OrderType]] = DHAN_ORDER_TYPE_WIRE.reverse
HONBA_SIDE_TO_DHAN: Final[dict[OrderSide, str]] = DHAN_SIDE_WIRE.forward
DHAN_SIDE_TO_HONBA: Final[dict[str, OrderSide]] = DHAN_SIDE_WIRE.reverse
HONBA_TIF_TO_DHAN: Final[dict[TimeInForce, str]] = DHAN_TIF_WIRE.forward

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

# Disconnect reason codes (int16 LE after the 8-byte header of a code-50 packet) that mean the
# token/plan is the problem, so reconnecting cannot help. From Dhan's Data API error list
# (https://dhanhq.co/docs/v2/annexure/ and the dhanhq client; not seen on a live feed): 805 too many
# connections (retryable, deliberately excluded), 806 Data APIs not subscribed, 807 access token expired,
# 808 authentication failed, 809 access token invalid, 810 client id invalid.
DHAN_FEED_AUTH_DISCONNECT_CODES: Final[frozenset[int]] = frozenset({806, 807, 808, 809, 810})
