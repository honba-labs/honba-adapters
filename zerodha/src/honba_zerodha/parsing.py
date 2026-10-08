"""Wire payload builders and response deserializers for Zerodha Kite Connect v3."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from honba.adapters.models import (
    DepthLevel,
    Funds,
    Holding,
    MarginReport,
    MarketDepth,
    OrderReport,
    Product,
)
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.money import Currency, Money
from honba.domain.order import OrderIntent, OrderSide, OrderStatus, OrderType, TimeInForce
from honba.domain.position import Position, PositionSide
from honba.domain.tick import QuoteTick
from honba.domain.trade import Trade

from honba_zerodha.constants import (
    ZERODHA_ORDER_TYPE_MAP,
    ZERODHA_PRODUCT_MAP,
    ZERODHA_REVERSE_ORDER_TYPE_MAP,
    ZERODHA_REVERSE_PRODUCT_MAP,
    ZERODHA_REVERSE_SIDE_MAP,
    ZERODHA_SIDE_MAP,
    ZERODHA_STATUS_MAP,
)


def parse_funds(data: dict[str, Any]) -> Funds:
    """Parse Kite Connect /user/margins equity response into canonical Funds."""
    eq = data.get("equity") or data
    avail = eq.get("available") or eq
    util = eq.get("utilised") or {}

    available_cash = float(avail.get("cash", 0.0))
    opening_bal = float(avail.get("opening_balance", available_cash))
    margin_used = float(util.get("debits", util.get("margin_used", 0.0)))
    collateral = float(avail.get("collateral", 0.0))

    return Funds(
        available_cash=available_cash,
        opening_balance=opening_bal,
        margin_used=margin_used,
        collateral=collateral,
        currency="INR",
    )


def parse_margin_report(data: dict[str, Any], instrument_id: InstrumentId) -> MarginReport:
    """Parse Kite Connect order margin response into canonical MarginReport."""
    initial = float(data.get("total", data.get("initial", 0.0)))
    maint = float(data.get("span", data.get("maintenance", 0.0)))
    maint = min(maint, initial)

    return MarginReport(
        instrument_id=instrument_id,
        initial=initial,
        maintenance=maint,
    )


def parse_holding(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> Holding:
    """Parse a Kite Connect /portfolio/holdings item into canonical Holding."""
    symbol = str(item.get("tradingsymbol", ""))
    exchange = str(item.get("exchange", "NSE"))
    inst_id = (resolver(symbol, exchange) if resolver else None) or InstrumentId(symbol, exchange)

    qty = float(item.get("quantity", 0.0))
    avg_price = float(item.get("average_price", 0.0))
    last_price = float(item.get("last_price", 0.0))

    return Holding(
        instrument_id=inst_id,
        quantity=max(0.0, qty),
        average_price=max(0.0, avg_price),
        last_price=max(0.0, last_price),
    )


def parse_position(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> Position:
    """Parse a Kite Connect /portfolio/positions item into canonical Position."""
    symbol = str(item.get("tradingsymbol", ""))
    exchange = str(item.get("exchange", "NSE"))
    inst_id = (resolver(symbol, exchange) if resolver else None) or InstrumentId(symbol, exchange)

    qty = float(item.get("quantity", 0.0))
    if qty > 0:
        side = PositionSide.LONG
        avg_price = float(item.get("buy_price", item.get("average_price", 0.0)))
    elif qty < 0:
        side = PositionSide.SHORT
        avg_price = float(item.get("sell_price", item.get("average_price", 0.0)))
    else:
        side = PositionSide.FLAT
        avg_price = float(item.get("average_price", 0.0))

    realized = float(item.get("realised", item.get("realized_pnl", 0.0)))

    return Position(
        instrument_id=inst_id,
        currency=Currency.INR,
        side=side,
        quantity=abs(qty),
        avg_price=max(0.0, avg_price),
        realized_pnl=Money.from_major(realized, Currency.INR),
    )


def parse_order(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> OrderReport:
    """Parse a Kite Connect /orders item into canonical OrderReport."""
    order_id = str(item.get("order_id", ""))
    symbol = str(item.get("tradingsymbol", ""))
    exchange = str(item.get("exchange", "NSE"))
    inst_id = (resolver(symbol, exchange) if resolver else None) or InstrumentId(symbol, exchange)

    txn_type = str(item.get("transaction_type", "BUY")).upper()
    side = ZERODHA_REVERSE_SIDE_MAP.get(txn_type, OrderSide.BUY)

    order_status_raw = str(item.get("status", "OPEN")).upper()
    status = ZERODHA_STATUS_MAP.get(order_status_raw, OrderStatus.SUBMITTED)

    prod_str = str(item.get("product", "CNC")).upper()
    product = ZERODHA_REVERSE_PRODUCT_MAP.get(prod_str, Product.DELIVERY)

    otype_str = str(item.get("order_type", "MARKET")).upper()
    order_type = ZERODHA_REVERSE_ORDER_TYPE_MAP.get(otype_str, OrderType.MARKET)

    qty = float(item.get("quantity", 0.0))
    filled_qty = float(item.get("filled_quantity", 0.0))
    limit_price = (
        float(item["price"]) if item.get("price") is not None and float(item["price"]) > 0 else None
    )
    trigger_price = (
        float(item["trigger_price"])
        if item.get("trigger_price") is not None and float(item["trigger_price"]) > 0
        else None
    )
    avg_price = float(item.get("average_price", 0.0) or 0.0)

    reject_reason = None
    if status is OrderStatus.REJECTED:
        reject_reason = str(
            item.get("status_message") or item.get("rejection_reason") or "Rejected by broker"
        )
    elif status is OrderStatus.FILLED:
        if filled_qty != qty:
            filled_qty = qty
        if avg_price <= 0.0:
            avg_price = limit_price or 1.0

    return OrderReport(
        order_id=order_id,
        instrument_id=inst_id,
        side=side,
        quantity=max(1.0, qty),
        status=status,
        product=product,
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        price=limit_price,
        trigger_price=trigger_price,
        filled_quantity=filled_qty,
        average_price=avg_price if filled_qty > 0 else 0.0,
        reject_reason=reject_reason,
    )


def parse_trade(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> Trade:
    """Parse a Kite Connect /trades item into canonical Trade."""
    order_id = str(item.get("order_id", ""))
    symbol = str(item.get("tradingsymbol", ""))
    exchange = str(item.get("exchange", "NSE"))
    inst_id = (resolver(symbol, exchange) if resolver else None) or InstrumentId(symbol, exchange)

    txn_type = str(item.get("transaction_type", "BUY")).upper()
    side = ZERODHA_REVERSE_SIDE_MAP.get(txn_type, OrderSide.BUY)

    qty = float(item.get("quantity", 0.0))
    price = float(item.get("average_price", item.get("price", 0.0)))
    brokerage = float(item.get("brokerage", 0.0))

    return Trade(
        instrument_id=inst_id,
        side=side,
        quantity=qty,
        price=price,
        order_id=order_id,
        costs=Money.from_major(brokerage, Currency.INR),
    )


def parse_quote(data: dict[str, Any], instrument_id: InstrumentId) -> QuoteTick:
    """Parse a Kite Connect /quote item into canonical QuoteTick."""
    depth = data.get("depth") or {}
    buy_levels = depth.get("buy") or []
    sell_levels = depth.get("sell") or []

    bid_price = float(buy_levels[0]["price"]) if buy_levels else float(data.get("last_price", 0.0))
    ask_price = (
        float(sell_levels[0]["price"]) if sell_levels else float(data.get("last_price", 0.0))
    )
    bid_size = float(buy_levels[0]["quantity"]) if buy_levels else 0.0
    ask_size = float(sell_levels[0]["quantity"]) if sell_levels else 0.0

    return QuoteTick(
        instrument_id=instrument_id,
        ts=time.time_ns(),
        bid_price=bid_price,
        ask_price=ask_price,
        bid_size=bid_size,
        ask_size=ask_size,
    )


def parse_depth(data: dict[str, Any], instrument_id: InstrumentId) -> MarketDepth:
    """Parse a Kite Connect /quote depth section into canonical MarketDepth (5 levels)."""
    depth = data.get("depth") or {}
    bids: list[DepthLevel] = [
        DepthLevel(price=float(lvl["price"]), quantity=float(lvl["quantity"]))
        for lvl in depth.get("buy", [])
        if float(lvl.get("price", 0)) > 0
    ]
    asks: list[DepthLevel] = [
        DepthLevel(price=float(lvl["price"]), quantity=float(lvl["quantity"]))
        for lvl in depth.get("sell", [])
        if float(lvl.get("price", 0)) > 0
    ]

    return MarketDepth(
        instrument_id=instrument_id,
        ts=time.time_ns(),
        bids=tuple(bids),
        asks=tuple(asks),
    )


def parse_historical_candles(
    data: dict[str, Any], instrument_id: InstrumentId, timeframe: str
) -> list[Bar]:
    """Parse Kite Connect /instruments/historical candles into canonical Bars."""
    candles = data.get("candles", []) if isinstance(data, dict) else data
    bars: list[Bar] = []

    for item in candles:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue

        raw_ts, o, h, l, c, v = item[:6]
        if isinstance(raw_ts, str):
            try:
                clean_ts = raw_ts.replace("Z", "+00:00")
                if len(clean_ts) >= 5 and clean_ts[-5] in ("+", "-") and ":" not in clean_ts[-5:]:
                    clean_ts = f"{clean_ts[:-2]}:{clean_ts[-2:]}"
                dt = datetime.fromisoformat(clean_ts)
                epoch_ns = int(dt.timestamp() * 1_000_000_000)
            except (ValueError, TypeError, OSError):
                epoch_ns = time.time_ns()
        elif isinstance(raw_ts, (int, float)):
            epoch_ns = int(raw_ts * 1_000_000_000)
        else:
            epoch_ns = time.time_ns()

        bars.append(
            Bar(
                instrument_id=instrument_id,
                ts=epoch_ns,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )
        )

    return bars


def build_order_payload(
    *,
    intent: OrderIntent,
    product: Product,
    client_order_id: str | None,
    tradingsymbol: str,
    exchange: str,
) -> dict[str, Any]:
    """Construct Kite Connect v3 order parameters."""
    txn_type = ZERODHA_SIDE_MAP.get(intent.side, "BUY")
    otype = ZERODHA_ORDER_TYPE_MAP.get(intent.order_type, "MARKET")
    prod = ZERODHA_PRODUCT_MAP.get(product, "CNC")

    payload: dict[str, Any] = {
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "transaction_type": txn_type,
        "order_type": otype,
        "quantity": int(intent.quantity),
        "product": prod,
        "validity": "DAY",
    }

    if intent.price is not None and intent.order_type in (
        OrderType.LIMIT,
        OrderType.STOP_LIMIT,
    ):
        payload["price"] = float(intent.price)

    if intent.trigger_price is not None and intent.order_type in (
        OrderType.STOP_LIMIT,
        OrderType.STOP_MARKET,
    ):
        payload["trigger_price"] = float(intent.trigger_price)

    if client_order_id:
        payload["tag"] = client_order_id[:20]

    return payload
