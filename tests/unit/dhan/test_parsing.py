"""Tests for Dhan response parsing and wire translation (M2)."""

from __future__ import annotations

import pytest
from honba.adapters.models import DepthLevel, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderStatus, OrderType
from honba.domain.position import PositionSide

from honba_adapters.dhan.parsing import (
    build_order_payload,
    parse_depth,
    parse_funds,
    parse_historical_bars,
    parse_holding,
    parse_margin_report,
    parse_order,
    parse_position,
    parse_quote,
    parse_trade,
)


def mock_id_resolver(sec_id: str, seg: str) -> InstrumentId | None:
    if sec_id == "1333":
        return InstrumentId("RELIANCE", "NSE")
    if sec_id == "11536":
        return InstrumentId("TCS", "NSE")
    return InstrumentId(sec_id, "NSE")


class TestDhanParsing:
    def test_parse_funds(self) -> None:
        data = {
            "availabelBalance": 500000.0,
            "sodLimit": 500000.0,
            "utilizedAmount": 50000.0,
            "collateralAmount": 20000.0,
            "receiveableAmount": 0.0,
            "blockedPayoutAmount": 0.0,
        }
        funds = parse_funds(data)
        assert funds.available_cash == 500000.0
        assert funds.opening_balance == 500000.0
        assert funds.margin_used == 50000.0
        assert funds.collateral == 20000.0
        assert funds.currency == "INR"

    def test_parse_margin_report(self) -> None:
        data = {"totalMargin": 75000.0, "maintenanceMargin": 60000.0}
        inst = InstrumentId("RELIANCE", "NSE")
        rep = parse_margin_report(data, inst)
        assert rep.initial == 75000.0
        assert rep.maintenance == 60000.0
        assert rep.headroom == 15000.0
        assert rep.instrument_id == inst

    def test_parse_holding(self) -> None:
        item = {
            "securityId": "1333",
            "exchangeSegment": "NSE_EQ",
            "totalQty": 100.0,
            "avgCostPrice": 2450.0,
            "ltp": 2500.0,
        }
        holding = parse_holding(item, mock_id_resolver)
        assert holding.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert holding.quantity == 100.0
        assert holding.average_price == 2450.0
        assert holding.last_price == 2500.0
        assert not holding.is_zero

    def test_parse_position(self) -> None:
        long_item = {
            "securityId": "1333",
            "exchangeSegment": "NSE_EQ",
            "netQty": 50.0,
            "costPrice": 2450.0,
            "realizedProfit": 1250.0,
        }
        pos = parse_position(long_item, mock_id_resolver)
        assert pos.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert pos.side == PositionSide.LONG
        assert pos.quantity == 50.0
        assert pos.avg_price == 2450.0
        assert pos.realized_pnl.to_major() == 1250.0

    def test_parse_order_accepted(self) -> None:
        item = {
            "orderId": "DHAN-1001",
            "securityId": "1333",
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "orderType": "LIMIT",
            "productType": "CNC",
            "orderStatus": "PENDING",
            "quantity": 10.0,
            "price": 2450.0,
            "filledQty": 0.0,
        }
        order = parse_order(item, mock_id_resolver)
        assert order.order_id == "DHAN-1001"
        assert order.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert order.side == OrderSide.BUY
        assert order.status == OrderStatus.ACCEPTED
        assert order.product == Product.DELIVERY
        assert order.order_type == OrderType.LIMIT
        assert order.price == 2450.0
        assert order.quantity == 10.0
        assert order.filled_quantity == 0.0

    def test_parse_order_rejected(self) -> None:
        item = {
            "orderId": "DHAN-1002",
            "securityId": "1333",
            "exchangeSegment": "NSE_EQ",
            "transactionType": "SELL",
            "orderType": "MARKET",
            "productType": "INTRADAY",
            "orderStatus": "REJECTED",
            "quantity": 5.0,
            "remarks": "Insufficient margin available in account",
        }
        order = parse_order(item, mock_id_resolver)
        assert order.order_id == "DHAN-1002"
        assert order.status == OrderStatus.REJECTED
        assert order.reject_reason == "Insufficient margin available in account"

    def test_parse_trade(self) -> None:
        item = {
            "orderId": "DHAN-1001",
            "securityId": "1333",
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "tradedQuantity": 10.0,
            "tradedPrice": 2450.0,
            "brokerageCharge": 15.5,
        }
        trade = parse_trade(item, mock_id_resolver)
        assert trade.order_id == "DHAN-1001"
        assert trade.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert trade.side == OrderSide.BUY
        assert trade.quantity == 10.0
        assert trade.price == 2450.0
        assert trade.costs.to_major() == pytest.approx(15.5)

    def test_parse_quote_and_depth(self) -> None:
        inst = InstrumentId("RELIANCE", "NSE")
        data = {
            "lastPrice": 2455.0,
            "depth": {
                "buy": [
                    {"price": 2454.0, "quantity": 100},
                    {"price": 2453.0, "quantity": 200},
                ],
                "sell": [
                    {"price": 2456.0, "quantity": 150},
                    {"price": 2457.0, "quantity": 250},
                ],
            },
        }
        quote = parse_quote(data, inst)
        assert quote.bid_price == 2454.0
        assert quote.ask_price == 2456.0
        assert quote.bid_size == 100.0
        assert quote.ask_size == 150.0

        depth = parse_depth(data, inst)
        assert len(depth.bids) == 2
        assert len(depth.asks) == 2
        assert depth.bids[0] == DepthLevel(2454.0, 100.0)
        assert depth.bids[1] == DepthLevel(2453.0, 200.0)
        assert depth.asks[0] == DepthLevel(2456.0, 150.0)
        assert depth.asks[1] == DepthLevel(2457.0, 250.0)
        assert depth.best_bid is not None and depth.best_bid.price == 2454.0
        assert depth.best_ask is not None and depth.best_ask.price == 2456.0
        assert depth.mid_price == 2455.0

    def test_parse_historical_bars(self) -> None:
        inst = InstrumentId("RELIANCE", "NSE")
        data = {
            "open": [2450.0, 2452.0],
            "high": [2455.0, 2458.0],
            "low": [2448.0, 2451.0],
            "close": [2452.0, 2456.0],
            "volume": [1000.0, 1500.0],
            "start_Time": [1700000000.0, 1700000060.0],
        }
        bars = parse_historical_bars(data, inst, timeframe="1m")
        assert len(bars) == 2
        assert bars[0].open == 2450.0
        assert bars[0].close == 2452.0
        assert bars[1].high == 2458.0

    def test_build_order_payload(self) -> None:
        inst = InstrumentId("RELIANCE", "NSE")
        intent = OrderIntent(
            instrument_id=inst,
            side=OrderSide.BUY,
            quantity=25.0,
            order_type=OrderType.LIMIT,
            price=2450.5,
        )
        payload = build_order_payload(
            intent=intent,
            product=Product.DELIVERY,
            client_order_id="CID-12345",
            security_id="1333",
            exchange_segment="NSE_EQ",
        )
        assert payload["securityId"] == "1333"
        assert payload["exchangeSegment"] == "NSE_EQ"
        assert payload["transactionType"] == "BUY"
        assert payload["orderType"] == "LIMIT"
        assert payload["productType"] == "CNC"
        assert payload["price"] == 2450.5
        assert payload["quantity"] == 25
        assert payload["correlationId"] == "CID-12345"
