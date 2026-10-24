"""Constants, wire mappings, and error codes for Angel One SmartAPI 2.0."""

from __future__ import annotations

from typing import Final

from honba.adapters.models import Product
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce

from honba_adapters.common.maps import WireMap

ANGELONE_DEFAULT_HTTP_ENDPOINT: Final[str] = "https://apiconnect.angelone.in"
ANGELONE_DEFAULT_WS_ENDPOINT: Final[str] = "wss://smartapisocket.angelone.in/smart-stream"
ANGELONE_ORDER_UPDATE_WS_ENDPOINT: Final[str] = "wss://tns.angelone.in/smart-order-update"
OPENAPI_SCRIP_MASTER_URL: Final[str] = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Canonical venue names recognized by SmartAPI 2.0
ANGELONE_EXCHANGES: Final[frozenset[str]] = frozenset({"NSE", "BSE", "NFO", "BFO", "MCX", "CDS"})

# SmartStream numeric exchange identifiers (Little-Endian byte 1)
SMARTSTREAM_EXCHANGE_TYPE_TO_NAME: Final[dict[int, str]] = {
    1: "NSE",
    2: "NFO",
    3: "BSE",
    4: "BFO",
    5: "MCX",
    7: "NCX",
    13: "CDS",
}

SMARTSTREAM_NAME_TO_EXCHANGE_TYPE: Final[dict[str, int]] = {
    "NSE": 1,
    "NFO": 2,
    "BSE": 3,
    "BFO": 4,
    "MCX": 5,
    "NCX": 7,
    "CDS": 13,
}

ANGELONE_SIDE_MAP: Final[dict[OrderSide, str]] = {
    OrderSide.BUY: "BUY",
    OrderSide.SELL: "SELL",
}
ANGELONE_REVERSE_SIDE_MAP: Final[dict[str, OrderSide]] = {
    "BUY": OrderSide.BUY,
    "SELL": OrderSide.SELL,
}

ANGELONE_ORDER_TYPE_MAP: Final[dict[OrderType, str]] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LIMIT: "STOPLOSS_LIMIT",
    OrderType.STOP_MARKET: "STOPLOSS_MARKET",
}
ANGELONE_REVERSE_ORDER_TYPE_MAP: Final[dict[str, OrderType]] = {
    "MARKET": OrderType.MARKET,
    "LIMIT": OrderType.LIMIT,
    "STOPLOSS_LIMIT": OrderType.STOP_LIMIT,
    "STOPLOSS_MARKET": OrderType.STOP_MARKET,
}

ANGELONE_PRODUCT_MAP: Final[dict[Product, str]] = {
    Product.DELIVERY: "DELIVERY",
    Product.INTRADAY: "INTRADAY",
    Product.CARRY: "CARRYFORWARD",
}
ANGELONE_REVERSE_PRODUCT_MAP: Final[dict[str, Product]] = {
    "DELIVERY": Product.DELIVERY,
    "CNC": Product.DELIVERY,
    "INTRADAY": Product.INTRADAY,
    "MIS": Product.INTRADAY,
    "CARRYFORWARD": Product.CARRY,
    "NRML": Product.CARRY,
    "MARGIN": Product.CARRY,
    "BO": Product.INTRADAY,
}

ANGELONE_TIF_MAP: Final[dict[TimeInForce, str]] = {
    TimeInForce.DAY: "DAY",
    TimeInForce.IOC: "IOC",
}
ANGELONE_REVERSE_TIF_MAP: Final[dict[str, TimeInForce]] = {
    "DAY": TimeInForce.DAY,
    "IOC": TimeInForce.IOC,
}

ANGELONE_STATUS_MAP: Final[dict[str, OrderStatus]] = {
    "COMPLETE": OrderStatus.FILLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
    "OPEN": OrderStatus.ACCEPTED,
    "MODIFIED": OrderStatus.ACCEPTED,
    "PENDING": OrderStatus.SUBMITTED,
    "TRIGGER_PENDING": OrderStatus.ACCEPTED,
}

# Historical bar interval strings expected by SmartAPI getCandleData
ANGELONE_TIMEFRAMES: Final[dict[str, str]] = {
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}

# Bidirectional coverage maps
ANGELONE_ORDER_TYPE_WIRE: Final[WireMap[OrderType]] = WireMap(ANGELONE_ORDER_TYPE_MAP)
ANGELONE_PRODUCT_WIRE: Final[WireMap[Product]] = WireMap(ANGELONE_PRODUCT_MAP)
ANGELONE_TIF_WIRE: Final[WireMap[TimeInForce]] = WireMap(ANGELONE_TIF_MAP)
ANGELONE_SIDE_WIRE: Final[WireMap[OrderSide]] = WireMap(ANGELONE_SIDE_MAP)

# Error code mappings for documented SmartAPI errors
ERROR_CODE_AUTH: Final[frozenset[str]] = frozenset({"AB1004", "AB1011", "AG8002_AUTH"})
ERROR_CODE_RATELIMIT: Final[frozenset[str]] = frozenset({"AB1010", "AB1012"})
ERROR_CODE_INVALID: Final[frozenset[str]] = frozenset({"AB2001", "AB2002"})
ERROR_CODE_REJECTED: Final[frozenset[str]] = frozenset(
    {"AG8001", "AG8002", "AG8003", "AB1005", "AB1006"}
)

__all__ = [
    "ANGELONE_DEFAULT_HTTP_ENDPOINT",
    "ANGELONE_DEFAULT_WS_ENDPOINT",
    "ANGELONE_EXCHANGES",
    "ANGELONE_ORDER_TYPE_MAP",
    "ANGELONE_ORDER_TYPE_WIRE",
    "ANGELONE_ORDER_UPDATE_WS_ENDPOINT",
    "ANGELONE_PRODUCT_MAP",
    "ANGELONE_PRODUCT_WIRE",
    "ANGELONE_REVERSE_ORDER_TYPE_MAP",
    "ANGELONE_REVERSE_PRODUCT_MAP",
    "ANGELONE_REVERSE_SIDE_MAP",
    "ANGELONE_REVERSE_TIF_MAP",
    "ANGELONE_SIDE_MAP",
    "ANGELONE_SIDE_WIRE",
    "ANGELONE_STATUS_MAP",
    "ANGELONE_TIF_MAP",
    "ANGELONE_TIF_WIRE",
    "ANGELONE_TIMEFRAMES",
    "ERROR_CODE_AUTH",
    "ERROR_CODE_INVALID",
    "ERROR_CODE_RATELIMIT",
    "ERROR_CODE_REJECTED",
    "OPENAPI_SCRIP_MASTER_URL",
    "SMARTSTREAM_EXCHANGE_TYPE_TO_NAME",
    "SMARTSTREAM_NAME_TO_EXCHANGE_TYPE",
]
