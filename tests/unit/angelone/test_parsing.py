"""Tests for Angel One zero-float parsing, decimal precision, and error mapping."""

from __future__ import annotations

from decimal import Decimal

import pytest
from honba.adapters.models import OrderStatus, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide, OrderType, TimeInForce
from honba.domain.position import PositionSide

from honba_adapters.angelone.parsing import (
    decimal_str,
    format_decimal,
    map_smartapi_error,
    parse_angelone_timestamp,
    parse_funds,
    parse_holding,
    parse_order,
    parse_position,
    whole_quantity,
)
from honba_adapters.common.errors import AuthenticationError, RateLimitExceeded


class TestAngelOneParsing:
    def test_decimal_str(self) -> None:
        assert decimal_str("2450.55") == Decimal("2450.55")
        assert decimal_str(100) == Decimal(100)
        assert decimal_str("-1") == Decimal(0)
        assert decimal_str("") == Decimal(0)
        assert decimal_str(None) == Decimal(0)

    def test_format_decimal(self) -> None:
        assert format_decimal(Decimal("1550.5"), 2) == "1550.50"
        assert format_decimal("1550.556", 2) == "1550.56"
        assert format_decimal(10, 2) == "10.00"

    def test_whole_quantity(self) -> None:
        assert whole_quantity(5.0) == 5
        assert whole_quantity(10) == 10
        assert whole_quantity("25") == 25
        with pytest.raises(ValueError, match="positive whole number"):
            whole_quantity(-1)
        with pytest.raises(ValueError, match="positive whole number"):
            whole_quantity(5.5)

    def test_parse_angelone_timestamp(self) -> None:
        ts1 = parse_angelone_timestamp("2023-11-15T09:15:00+05:30")
        assert ts1 > 0
        ts2 = parse_angelone_timestamp("2016-07-14T09:20:00 IST")
        assert ts2 > 0
        ts3 = parse_angelone_timestamp("09:45:11")
        assert ts3 > 0

    def test_parse_funds(self) -> None:
        raw = {
            "equityamount": "2340.40",
            "equitylimit": "10000.00",
            "commodityamount": "100.00",
        }
        funds = parse_funds(raw)
        assert funds.available_cash == 10000.00
        assert funds.margin_used == 7659.60

    def test_parse_position(self) -> None:
        raw_short = {
            "exchange": "NSE",
            "symbolname": "SBIN",
            "symboltoken": "3045",
            "netqty": "-2",
            "netprice": "19500.55",
            "pnl": "-10.5",
        }
        pos = parse_position(raw_short)
        assert pos.instrument_id == InstrumentId("SBIN", "NSE")
        assert pos.side is PositionSide.SHORT
        assert pos.quantity == 2.0
        assert pos.avg_price == 19500.55

    def test_parse_holding(self) -> None:
        raw = {
            "tradingsymbol": "SBIN-EQ",
            "exchange": "NSE",
            "quantity": "50",
            "averageprice": "500.00",
            "ltp": "550.00",
        }
        h = parse_holding(raw)
        assert h.instrument_id == InstrumentId("SBIN", "NSE")
        assert h.quantity == 50.0
        assert h.average_price == 500.00
        assert h.last_price == 550.00

    def test_parse_order(self) -> None:
        raw = {
            "orderid": "201130000006424",
            "exchange": "NSE",
            "symboltoken": "3045",
            "producttype": "DELIVERY",
            "tradingsymbol": "SBIN-EQ",
            "duration": "DAY",
            "price": "19500.55",
            "triggerprice": "-1",
            "quantity": "1",
            "transactiontype": "BUY",
            "ordertype": "LIMIT",
            "status": "REJECTED",
            "text": "Insufficient funds",
        }
        order = parse_order(raw)
        assert order.order_id == "201130000006424"
        assert order.instrument_id == InstrumentId("SBIN", "NSE")
        assert order.status is OrderStatus.REJECTED
        assert order.reject_reason == "Insufficient funds"
        assert order.order_type is OrderType.LIMIT
        assert order.side is OrderSide.BUY
        assert order.product is Product.DELIVERY
        assert order.time_in_force is TimeInForce.DAY
        assert order.price == 19500.55
        assert order.trigger_price is None

    def test_map_smartapi_error(self) -> None:
        assert isinstance(map_smartapi_error(401, "AB1004", "Invalid token"), AuthenticationError)
        assert isinstance(
            map_smartapi_error(429, "AB1010", "Request limit reached"), RateLimitExceeded
        )
        assert isinstance(map_smartapi_error(200, "AG8001", "Generic broker rejection"), Exception)
