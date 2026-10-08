"""Tests for Zerodha response parsing and wire translation (M3)."""

from __future__ import annotations

import pytest
from honba.adapters.models import DepthLevel, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderStatus, OrderType
from honba.domain.position import PositionSide

from honba_zerodha.parsing import (
    build_order_payload,
    parse_depth,
    parse_funds,
    parse_historical_candles,
    parse_holding,
    parse_margin_report,
    parse_order,
    parse_position,
    parse_quote,
    parse_trade,
)


def mock_id_resolver(symbol: str, exchange: str) -> InstrumentId | None:
    return InstrumentId(symbol, exchange)


class TestZerodhaParsing:
    def test_parse_funds(self) -> None:
        data = {
            "equity": {
                "net": 1000000.0,
                "available": {
                    "cash": 950000.0,
                    "opening_balance": 1000000.0,
                    "collateral": 25000.0,
                },
                "utilised": {
                    "debits": 50000.0,
                    "margin_used": 50000.0,
                },
            }
        }
        funds = parse_funds(data)
        assert funds.available_cash == 950000.0
        assert funds.opening_balance == 1000000.0
        assert funds.margin_used == 50000.0
        assert funds.collateral == 25000.0
        assert funds.currency == "INR"

    def test_parse_margin_report(self) -> None:
        data = {"total": 60000.0, "span": 45000.0, "available": 100000.0}
        inst = InstrumentId("RELIANCE", "NSE")
        rep = parse_margin_report(data, inst)
        assert rep.initial == 60000.0
        assert rep.maintenance == 45000.0
        assert rep.headroom == 15000.0
        assert rep.instrument_id == inst

    def test_parse_holding(self) -> None:
        item = {
            "tradingsymbol": "INFY",
            "exchange": "NSE",
            "quantity": 100.0,
            "average_price": 1450.0,
            "last_price": 1500.0,
        }
        holding = parse_holding(item, mock_id_resolver)
        assert holding.instrument_id == InstrumentId("INFY", "NSE")
        assert holding.quantity == 100.0
        assert holding.average_price == 1450.0
        assert holding.last_price == 1500.0
        assert not holding.is_zero

    def test_parse_position(self) -> None:
        long_item = {
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "quantity": 50.0,
            "buy_price": 2450.0,
            "realised": 1250.0,
            "unrealised": 500.0,
        }
        pos = parse_position(long_item, mock_id_resolver)
        assert pos.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert pos.side == PositionSide.LONG
        assert pos.quantity == 50.0
        assert pos.avg_price == 2450.0
        assert pos.realized_pnl.to_major() == 1250.0

    def test_parse_order_accepted(self) -> None:
        item = {
            "order_id": "KITE-1001",
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "LIMIT",
            "product": "CNC",
            "status": "OPEN",
            "quantity": 10.0,
            "price": 2450.0,
            "filled_quantity": 0.0,
        }
        order = parse_order(item, mock_id_resolver)
        assert order.order_id == "KITE-1001"
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
            "order_id": "KITE-1002",
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "transaction_type": "SELL",
            "order_type": "MARKET",
            "product": "MIS",
            "status": "REJECTED",
            "quantity": 5.0,
            "status_message": "RMS: Insufficient funds for execution",
        }
        order = parse_order(item, mock_id_resolver)
        assert order.order_id == "KITE-1002"
        assert order.status == OrderStatus.REJECTED
        assert order.reject_reason == "RMS: Insufficient funds for execution"

    def test_parse_trade(self) -> None:
        item = {
            "trade_id": "T-5555",
            "order_id": "KITE-1001",
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "transaction_type": "BUY",
            "quantity": 10.0,
            "average_price": 2450.0,
            "brokerage": 20.0,
        }
        trade = parse_trade(item, mock_id_resolver)
        assert trade.order_id == "KITE-1001"
        assert trade.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert trade.side == OrderSide.BUY
        assert trade.quantity == 10.0
        assert trade.price == 2450.0
        assert trade.costs.to_major() == pytest.approx(20.0)

    def test_parse_quote_and_depth(self) -> None:
        inst = InstrumentId("RELIANCE", "NSE")
        data = {
            "last_price": 2455.0,
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
        assert depth.best_bid.price == 2454.0
        assert depth.best_ask.price == 2456.0

    def test_parse_historical_candles(self) -> None:
        inst = InstrumentId("RELIANCE", "NSE")
        data = {
            "candles": [
                ["2023-11-15T09:15:00+05:30", 2450.0, 2455.0, 2448.0, 2452.0, 1000.0, 0],
                ["2023-11-15T09:16:00+05:30", 2452.0, 2458.0, 2451.0, 2456.0, 1500.0, 0],
            ]
        }
        bars = parse_historical_candles(data, inst, timeframe="1m")
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
            tradingsymbol="RELIANCE",
            exchange="NSE",
        )
        assert payload["tradingsymbol"] == "RELIANCE"
        assert payload["exchange"] == "NSE"
        assert payload["transaction_type"] == "BUY"
        assert payload["order_type"] == "LIMIT"
        assert payload["product"] == "CNC"
        assert payload["price"] == 2450.5
        assert payload["quantity"] == 25
        assert payload["tag"] == "CID-12345"
