"""Wire format converters and parsers for DhanHQ responses (M2).

Translates Dhan JSON dictionaries into Honba canonical domain types.
Zero Dhan wire types escape past this module boundary.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
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
from honba.domain.order import (
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from honba.domain.position import Position, PositionSide
from honba.domain.tick import QuoteTick
from honba.domain.trade import Trade

from honba_adapters.dhan.constants import (
    DHAN_ORDER_TYPE_TO_HONBA,
    DHAN_PRODUCT_TO_HONBA,
    DHAN_SIDE_TO_HONBA,
    DHAN_STATUS_TO_HONBA,
    HONBA_ORDER_TYPE_TO_DHAN,
    HONBA_PRODUCT_TO_DHAN,
    HONBA_SIDE_TO_DHAN,
    HONBA_TIF_TO_DHAN,
)

__all__ = [
    "build_order_payload",
    "parse_depth",
    "parse_funds",
    "parse_historical_bars",
    "parse_holding",
    "parse_margin_report",
    "parse_order",
    "parse_position",
    "parse_quote",
    "parse_trade",
]


def parse_funds(data: dict[str, Any]) -> Funds:
    """Parse Dhan /fundlimit JSON into canonical Funds."""
    avail = float(data.get("availabelBalance") or data.get("availableBalance") or 0.0)
    sod = float(data.get("sodLimit") or 0.0)
    utilized = float(data.get("utilizedAmount") or 0.0)
    collateral = float(data.get("collateralAmount") or 0.0)
    payin = float(data.get("receiveableAmount") or 0.0)
    payout = float(data.get("blockedPayoutAmount") or 0.0)

    opening = sod if sod > 0.0 else max(avail + utilized, 0.0)

    return Funds(
        available_cash=max(0.0, avail),
        opening_balance=max(0.0, opening),
        currency="INR",
        margin_used=max(0.0, utilized),
        collateral=max(0.0, collateral),
        payin=max(0.0, payin),
        payout=max(0.0, payout),
    )


def parse_margin_report(
    data: dict[str, Any], instrument_id: InstrumentId | None = None
) -> MarginReport:
    """Parse margin requirement response into canonical MarginReport."""
    total_margin = float(
        data.get("totalMargin") or data.get("requiredMargin") or data.get("margin") or 0.0
    )
    maint_margin = float(data.get("maintenanceMargin") or (total_margin * 0.8))
    maint_margin = min(maint_margin, total_margin)

    return MarginReport(
        initial=max(0.0, total_margin),
        maintenance=max(0.0, maint_margin),
        currency="INR",
        instrument_id=instrument_id,
    )


def parse_holding(
    item: dict[str, Any],
    id_resolver: Callable[[str, str], InstrumentId | None],
) -> Holding:
    """Parse one item from Dhan /holdings into canonical Holding."""
    sec_id = str(item.get("securityId") or "")
    seg = str(item.get("exchangeSegment") or item.get("exchange") or "NSE_EQ")
    inst_id = id_resolver(sec_id, seg)
    if inst_id is None:
        inst_id = InstrumentId(sec_id, "NSE")

    qty = float(item.get("totalQty") or item.get("holdingQty") or 0.0)
    avg_price = float(item.get("avgCostPrice") or 0.0)
    ltp = float(item.get("ltp") or 0.0)

    return Holding(
        instrument_id=inst_id,
        quantity=max(0.0, qty),
        average_price=max(0.0, avg_price),
        last_price=max(0.0, ltp),
    )


def parse_position(
    item: dict[str, Any],
    id_resolver: Callable[[str, str], InstrumentId | None],
) -> Position:
    """Parse one item from Dhan /positions into canonical Position."""
    sec_id = str(item.get("securityId") or "")
    seg = str(item.get("exchangeSegment") or "NSE_EQ")
    inst_id = id_resolver(sec_id, seg)
    if inst_id is None:
        inst_id = InstrumentId(sec_id, "NSE")

    net_qty = float(item.get("netQty") or 0.0)
    side = PositionSide.LONG if net_qty >= 0.0 else PositionSide.SHORT
    abs_qty = abs(net_qty)
    avg_price = float(item.get("costPrice") or item.get("buyAvg") or item.get("sellAvg") or 0.0)
    realized_pnl = float(item.get("realizedProfit") or item.get("realizedPnl") or 0.0)

    return Position(
        instrument_id=inst_id,
        currency=Currency.INR,
        side=side,
        quantity=abs_qty,
        avg_price=max(0.0, avg_price),
        realized_pnl=Money.from_major(realized_pnl, Currency.INR),
    )


def parse_order(
    item: dict[str, Any],
    id_resolver: Callable[[str, str], InstrumentId | None],
) -> OrderReport:
    """Parse one order from Dhan /orders into canonical OrderReport."""
    order_id = str(item.get("orderId") or item.get("order_id") or "")
    sec_id = str(item.get("securityId") or "")
    seg = str(item.get("exchangeSegment") or "NSE_EQ")
    inst_id = id_resolver(sec_id, seg)
    if inst_id is None:
        inst_id = InstrumentId(sec_id, "NSE")

    side_str = str(item.get("transactionType") or "BUY").upper()
    side = DHAN_SIDE_TO_HONBA.get(side_str, OrderSide.BUY)

    order_status_str = str(item.get("orderStatus") or "PENDING").upper()
    status = DHAN_STATUS_TO_HONBA.get(order_status_str, OrderStatus.ACCEPTED)

    prod_str = str(item.get("productType") or "CNC").upper()
    product = DHAN_PRODUCT_TO_HONBA.get(prod_str, Product.DELIVERY)

    order_type_str = str(item.get("orderType") or "MARKET").upper()
    order_type = DHAN_ORDER_TYPE_TO_HONBA.get(order_type_str, OrderType.MARKET)

    qty = float(item.get("quantity") or 1.0)
    filled_qty = float(item.get("filledQty") or item.get("tradedQuantity") or 0.0)
    avg_price = float(item.get("tradedPrice") or item.get("averagePrice") or 0.0)
    limit_price = (
        float(item["price"]) if item.get("price") is not None and float(item["price"]) > 0 else None
    )
    trigger_price = (
        float(item["triggerPrice"])
        if item.get("triggerPrice") is not None and float(item["triggerPrice"]) > 0
        else None
    )

    reject_reason = None
    if status is OrderStatus.REJECTED:
        reject_reason = (
            item.get("omsErrorDescription")
            or item.get("rejectionReason")
            or item.get("remarks")
            or "Rejected by broker RMS"
        )
    elif status is OrderStatus.FILLED and filled_qty != qty:
        filled_qty = qty

    return OrderReport(
        order_id=order_id,
        instrument_id=inst_id,
        side=side,
        quantity=max(1.0, qty),
        status=status,
        product=product,
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        filled_quantity=filled_qty,
        average_price=avg_price if filled_qty > 0 else 0.0,
        price=limit_price,
        trigger_price=trigger_price,
        reject_reason=reject_reason,
        ts_event=0,
    )


def parse_trade(
    item: dict[str, Any],
    id_resolver: Callable[[str, str], InstrumentId | None],
) -> Trade:
    """Parse one fill from Dhan /trades into canonical Trade."""
    order_id = str(item.get("orderId") or item.get("order_id") or "")
    sec_id = str(item.get("securityId") or "")
    seg = str(item.get("exchangeSegment") or "NSE_EQ")
    inst_id = id_resolver(sec_id, seg)
    if inst_id is None:
        inst_id = InstrumentId(sec_id, "NSE")

    side_str = str(item.get("transactionType") or "BUY").upper()
    side = DHAN_SIDE_TO_HONBA.get(side_str, OrderSide.BUY)

    qty = float(item.get("tradedQuantity") or item.get("quantity") or 1.0)
    price = float(item.get("tradedPrice") or item.get("price") or 0.0)
    costs = float(item.get("brokerageCharge") or item.get("charges") or 0.0)

    return Trade(
        instrument_id=inst_id,
        side=side,
        quantity=qty,
        price=price,
        order_id=order_id,
        costs=Money.from_major(costs, Currency.INR),
    )


def parse_quote(data: dict[str, Any], instrument_id: InstrumentId) -> QuoteTick:
    """Parse Dhan quote payload into canonical QuoteTick."""
    depth_data = data.get("depth", {})
    buy_depth = depth_data.get("buy", [])
    sell_depth = depth_data.get("sell", [])

    bid_price = float(buy_depth[0].get("price", 0.0)) if buy_depth else float(data.get("bid", 0.0))
    bid_qty = (
        float(buy_depth[0].get("quantity", 0.0)) if buy_depth else float(data.get("bidQty", 0.0))
    )
    ask_price = (
        float(sell_depth[0].get("price", 0.0)) if sell_depth else float(data.get("ask", 0.0))
    )
    ask_qty = (
        float(sell_depth[0].get("quantity", 0.0)) if sell_depth else float(data.get("askQty", 0.0))
    )

    ltp = float(data.get("lastPrice") or data.get("ltp") or 0.0)
    if bid_price <= 0.0 and ltp > 0.0:
        bid_price = ltp
    if ask_price <= 0.0 and ltp > 0.0:
        ask_price = ltp

    if bid_price > ask_price > 0.0:
        bid_price = ask_price

    now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)

    return QuoteTick(
        instrument_id=instrument_id,
        ts=now_ns,
        bid_price=max(0.0, bid_price),
        ask_price=max(0.0, ask_price),
        bid_size=max(0.0, bid_qty),
        ask_size=max(0.0, ask_qty),
    )


def parse_depth(data: dict[str, Any], instrument_id: InstrumentId) -> MarketDepth:
    """Parse Dhan quote depth or binary depth packet into canonical MarketDepth."""
    depth_obj = data.get("depth", data)
    raw_bids = depth_obj.get("buy", depth_obj.get("bids", []))
    raw_asks = depth_obj.get("sell", depth_obj.get("asks", []))

    bids: list[DepthLevel] = []
    for b in raw_bids:
        p = float(b.get("price", 0.0))
        q = float(b.get("quantity", b.get("qty", 0.0)))
        if p > 0.0 and q >= 0.0:
            bids.append(DepthLevel(price=p, quantity=q))

    asks: list[DepthLevel] = []
    for a in raw_asks:
        p = float(a.get("price", 0.0))
        q = float(a.get("quantity", a.get("qty", 0.0)))
        if p > 0.0 and q >= 0.0:
            asks.append(DepthLevel(price=p, quantity=q))

    bids.sort(key=lambda x: x.price, reverse=True)
    asks.sort(key=lambda x: x.price)

    if bids and asks and bids[0].price > asks[0].price:
        bids = [b for b in bids if b.price < asks[0].price]

    now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)
    return MarketDepth(
        instrument_id=instrument_id,
        ts=now_ns,
        bids=tuple(bids),
        asks=tuple(asks),
    )


def parse_historical_bars(
    data: dict[str, Any], instrument_id: InstrumentId, timeframe: str
) -> list[Bar]:
    """Parse Dhan historical candles response into canonical list of Bar objects."""
    opens = data.get("open", [])
    highs = data.get("high", [])
    lows = data.get("low", [])
    closes = data.get("close", [])
    volumes = data.get("volume", [])
    timestamps = data.get("start_Time", data.get("timestamp", []))

    bars: list[Bar] = []
    n = min(len(opens), len(highs), len(lows), len(closes), len(volumes), len(timestamps))

    for i in range(n):
        ts_sec = float(timestamps[i])
        ts_ns = int(ts_sec * 1_000_000_000)
        bars.append(
            Bar(
                instrument_id=instrument_id,
                ts=ts_ns,
                open=float(opens[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                close=float(closes[i]),
                volume=float(volumes[i]),
            )
        )

    bars.sort(key=lambda b: b.ts)
    return bars


def build_order_payload(
    intent: OrderIntent,
    product: Product,
    client_order_id: str | None,
    security_id: str,
    exchange_segment: str,
) -> dict[str, Any]:
    """Build a Dhan v2 POST /orders JSON payload from canonical OrderIntent."""
    dhan_product = HONBA_PRODUCT_TO_DHAN.get(product, "CNC")
    dhan_order_type = HONBA_ORDER_TYPE_TO_DHAN.get(intent.order_type, "MARKET")
    dhan_side = HONBA_SIDE_TO_DHAN.get(intent.side, "BUY")

    payload: dict[str, Any] = {
        "securityId": security_id,
        "exchangeSegment": exchange_segment,
        "transactionType": dhan_side,
        "orderType": dhan_order_type,
        "productType": dhan_product,
        "validity": HONBA_TIF_TO_DHAN.get(intent.time_in_force, "DAY"),
        "quantity": int(intent.quantity),
    }

    if intent.price is not None and intent.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
        payload["price"] = float(intent.price)

    if intent.trigger_price is not None and intent.order_type in (
        OrderType.STOP_LIMIT,
        OrderType.STOP_MARKET,
    ):
        payload["triggerPrice"] = float(intent.trigger_price)

    if client_order_id:
        payload["correlationId"] = client_order_id[:25]

    return payload
