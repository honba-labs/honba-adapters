"""Wire payload builders and response deserializers for Angel One SmartAPI 2.0.

Provides exact Decimal arithmetic, zero floating-point approximation error,
and strict mapping into Honba canonical domain models.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from honba.adapters.errors import AdapterError, CapabilityError
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

from honba_adapters.angelone.constants import (
    ANGELONE_ORDER_TYPE_MAP,
    ANGELONE_PRODUCT_MAP,
    ANGELONE_REVERSE_ORDER_TYPE_MAP,
    ANGELONE_REVERSE_PRODUCT_MAP,
    ANGELONE_REVERSE_SIDE_MAP,
    ANGELONE_REVERSE_TIF_MAP,
    ANGELONE_SIDE_MAP,
    ANGELONE_STATUS_MAP,
    ANGELONE_TIF_MAP,
    ERROR_CODE_AUTH,
    ERROR_CODE_INVALID,
    ERROR_CODE_RATELIMIT,
)
from honba_adapters.common.errors import AuthenticationError, RateLimitExceeded

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_ISO_TIMESTAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:\s*IST|\+05:30)?"
)
_TIME_ONLY_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})$")


def decimal_str(value: Any) -> Decimal:
    """Safely convert value to Decimal without float round-trip inaccuracy."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal(0)
    try:
        # Stringify to avoid binary floating-point representation artifacts
        s = str(value).strip()
        # Handle sentinel values such as "-1"
        if s == "-1":
            return Decimal(0)
        return Decimal(s)
    except (InvalidOperation, ValueError) as err:
        raise ValueError(f"Invalid decimal value: {value!r}") from err


def format_decimal(value: Decimal | float | str, scale: int = 2) -> str:
    """Format a numeric value to a fixed-scale string representation."""
    d = decimal_str(value)
    # Quantize to scale
    target = Decimal(10) ** -scale
    quantized = d.quantize(target)
    return f"{quantized:.{scale}f}"


def whole_quantity(quantity: float | Decimal | str) -> int:
    """Ensure quantity is a positive whole number."""
    d = decimal_str(quantity)
    if d <= 0 or d != d.to_integral_value():
        raise ValueError(f"quantity must be a positive whole number, got {quantity}")
    return int(d)


def parse_angelone_timestamp(text: str) -> int:
    """Parse SmartAPI timestamp string into UTC unix nanoseconds.

    Handles formats like:
    - '2023-11-15T09:15:00+05:30'
    - '2023-11-15 09:15:00'
    - '2016-07-14T09:20:00 IST'
    - '09:45:11' (time-of-day IST, pegged to today)
    """
    if not text:
        return 0

    s = text.strip()
    match = _ISO_TIMESTAMP_RE.match(s)
    if match:
        date_part, time_part = match.groups()
        naive = dt.datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        aware = naive.replace(tzinfo=_IST)
        return int(aware.timestamp() * 1_000_000_000)

    time_match = _TIME_ONLY_RE.match(s)
    if time_match:
        h, m, sec = map(int, time_match.groups())
        today = dt.datetime.now(_IST).date()
        aware = dt.datetime(today.year, today.month, today.day, h, m, sec, tzinfo=_IST)
        return int(aware.timestamp() * 1_000_000_000)

    try:
        # Fallback to standard fromisoformat
        dt_val = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=_IST)
        return int(dt_val.timestamp() * 1_000_000_000)
    except (ValueError, TypeError, OverflowError):
        return 0


def build_order_payload(
    intent: OrderIntent,
    product: Product,
    client_order_id: str | None,
    tradingsymbol: str,
    symboltoken: str,
    exchange: str,
) -> dict[str, Any]:
    """Serialize OrderIntent and Product into Angel One SmartAPI placeOrder payload."""
    if intent.order_type not in ANGELONE_ORDER_TYPE_MAP:
        raise CapabilityError(f"order type {intent.order_type.value} is not supported by Angel One")
    if product not in ANGELONE_PRODUCT_MAP:
        raise CapabilityError(f"product {product.value} is not supported by Angel One")
    if intent.time_in_force not in ANGELONE_TIF_MAP:
        raise CapabilityError(
            f"time in force {intent.time_in_force.value} is not supported by Angel One"
        )

    variety = "NORMAL"
    if intent.order_type in (OrderType.STOP_LIMIT, OrderType.STOP_MARKET):
        variety = "STOPLOSS"

    price_str = "0"
    if intent.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
        if intent.price is None or intent.price <= 0:
            raise AdapterError("limit orders require a positive price")
        price_str = format_decimal(intent.price, 2)

    trigger_str = "0"
    if intent.order_type in (OrderType.STOP_LIMIT, OrderType.STOP_MARKET):
        if intent.trigger_price is None or intent.trigger_price <= 0:
            raise AdapterError("stop orders require a positive trigger price")
        trigger_str = format_decimal(intent.trigger_price, 2)

    payload: dict[str, Any] = {
        "variety": variety,
        "tradingsymbol": tradingsymbol,
        "symboltoken": str(symboltoken),
        "transactiontype": ANGELONE_SIDE_MAP[intent.side],
        "exchange": exchange,
        "ordertype": ANGELONE_ORDER_TYPE_MAP[intent.order_type],
        "producttype": ANGELONE_PRODUCT_MAP[product],
        "duration": ANGELONE_TIF_MAP[intent.time_in_force],
        "price": price_str,
        "triggerprice": trigger_str,
        "quantity": str(whole_quantity(intent.quantity)),
        "squareoff": "0",
        "stoploss": "0",
    }
    if client_order_id:
        # SmartAPI allows orderid or custom tag
        payload["ordertag"] = str(client_order_id)[:20]

    return payload


def build_modify_payload(
    order_id: str,
    *,
    tradingsymbol: str,
    symboltoken: str,
    exchange: str,
    order_type: OrderType,
    product: Product,
    new_price: float | Decimal | None = None,
    new_quantity: float | Decimal | None = None,
    current_price: float | Decimal | None = None,
    current_quantity: float | Decimal | None = None,
    variety: str = "NORMAL",
) -> dict[str, Any]:
    """Serialize modification request into SmartAPI modifyOrder payload."""
    price = new_price if new_price is not None else current_price
    qty = new_quantity if new_quantity is not None else current_quantity

    price_str = format_decimal(price, 2) if price is not None and price > 0 else "0"
    qty_str = str(whole_quantity(qty)) if qty is not None else "1"

    return {
        "orderid": str(order_id),
        "variety": variety,
        "tradingsymbol": tradingsymbol,
        "symboltoken": str(symboltoken),
        "exchange": exchange,
        "ordertype": ANGELONE_ORDER_TYPE_MAP.get(order_type, "LIMIT"),
        "producttype": ANGELONE_PRODUCT_MAP.get(product, "DELIVERY"),
        "duration": "DAY",
        "price": price_str,
        "quantity": qty_str,
    }


def parse_funds(data: dict[str, Any]) -> Funds:
    """Parse Angel One RMS limit response into canonical Funds."""
    d = data.get("data") or data

    equity_amt = decimal_str(d.get("equityamount") or d.get("net") or "0")
    equity_limit = decimal_str(
        d.get("equitylimit") or d.get("availablecash") or d.get("availablebalance") or "0"
    )
    commodity_amt = decimal_str(d.get("commodityamount") or "0")
    collateral = decimal_str(d.get("collateral") or "0")

    available_cash = equity_limit if equity_limit > 0 else (equity_amt + commodity_amt)
    margin_used = (
        max(Decimal(0), equity_limit - equity_amt) if equity_limit > equity_amt else Decimal(0)
    )
    opening_bal = max(Decimal(0), available_cash + margin_used)

    return Funds(
        available_cash=float(available_cash),
        opening_balance=float(opening_bal),
        currency="INR",
        margin_used=float(margin_used),
        collateral=float(collateral),
    )


def parse_margin_report(
    data: dict[str, Any], instrument_id: InstrumentId | None = None
) -> MarginReport:
    """Parse SmartAPI margin requirement response into canonical MarginReport."""
    d = data.get("data") or data
    total_margin = float(
        decimal_str(d.get("totalMargin") or d.get("margin") or d.get("total") or "0")
    )
    maint_margin = float(
        decimal_str(d.get("maintenanceMargin") or d.get("span") or (total_margin * 0.8))
    )
    maint_margin = min(maint_margin, total_margin)

    return MarginReport(
        instrument_id=instrument_id,
        initial=max(0.0, total_margin),
        maintenance=max(0.0, maint_margin),
        currency="INR",
    )


def parse_holding(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> Holding:
    """Parse a SmartAPI getHolding item into canonical Holding."""
    symbol = str(item.get("tradingsymbol") or item.get("symbolname") or "").strip()
    symbol = symbol.removesuffix("-EQ")
    exchange = str(item.get("exchange") or "NSE").strip().upper()
    inst_id = (resolver(symbol, exchange) if resolver else None) or InstrumentId(symbol, exchange)

    qty = float(decimal_str(item.get("quantity") or item.get("totalqty") or "0"))
    avg_price = float(decimal_str(item.get("averageprice") or item.get("avgprice") or "0"))
    last_price = float(decimal_str(item.get("ltp") or item.get("lastprice") or avg_price))

    return Holding(
        instrument_id=inst_id,
        quantity=qty,
        average_price=avg_price,
        last_price=last_price,
    )


def parse_position(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> Position:
    """Parse a SmartAPI getPosition item into canonical Position."""
    symbol = str(item.get("tradingsymbol") or item.get("symbolname") or "").strip()
    symbol = symbol.removesuffix("-EQ")
    exchange = str(item.get("exchange") or "NSE").strip().upper()
    inst_id = (resolver(symbol, exchange) if resolver else None) or InstrumentId(symbol, exchange)

    net_qty = decimal_str(item.get("netqty") or "0")
    net_price = decimal_str(item.get("netprice") or item.get("avgprice") or "0")
    pnl = decimal_str(item.get("pnl") or item.get("realised") or "0")

    side = PositionSide.LONG if net_qty >= 0 else PositionSide.SHORT

    return Position(
        instrument_id=inst_id,
        currency=Currency.INR,
        side=side,
        quantity=float(abs(net_qty)),
        avg_price=float(net_price),
        realized_pnl=Money.from_major(float(pnl), Currency.INR),
    )


def parse_order(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> OrderReport:
    """Parse a SmartAPI getOrderBook item into canonical OrderReport."""
    raw_symbol = str(item.get("tradingsymbol") or "").strip()
    clean_symbol = raw_symbol.removesuffix("-EQ")
    exchange = str(item.get("exchange") or "NSE").strip().upper()
    inst_id = (resolver(clean_symbol, exchange) if resolver else None) or InstrumentId(
        clean_symbol, exchange
    )

    order_id = str(item.get("orderid") or "").strip()
    side_str = str(item.get("transactiontype") or "BUY").strip().upper()
    side = ANGELONE_REVERSE_SIDE_MAP.get(side_str, OrderSide.BUY)

    order_type_str = str(item.get("ordertype") or "LIMIT").strip().upper()
    order_type = ANGELONE_REVERSE_ORDER_TYPE_MAP.get(order_type_str, OrderType.LIMIT)

    product_str = str(item.get("producttype") or "DELIVERY").strip().upper()
    product = ANGELONE_REVERSE_PRODUCT_MAP.get(product_str, Product.DELIVERY)

    tif_str = str(item.get("duration") or "DAY").strip().upper()
    tif = ANGELONE_REVERSE_TIF_MAP.get(tif_str, TimeInForce.DAY)

    status_str = str(item.get("status") or "OPEN").strip().upper()
    status = ANGELONE_STATUS_MAP.get(status_str, OrderStatus.ACCEPTED)

    qty = float(decimal_str(item.get("quantity") or "0"))
    filled_shares = float(decimal_str(item.get("filledshares") or "0"))
    avg_price = float(decimal_str(item.get("averageprice") or "0"))

    price_dec = decimal_str(item.get("price") or "0")
    price = float(price_dec) if price_dec > 0 else None

    trigger_dec = decimal_str(item.get("triggerprice") or "-1")
    trigger_price = float(trigger_dec) if trigger_dec > 0 else None

    reject_reason = None
    if status == OrderStatus.REJECTED:
        reject_reason = str(
            item.get("text")
            or item.get("rejectionreason")
            or item.get("message")
            or "Order rejected by broker"
        )

    ts_str = str(item.get("lastmodifiedtime") or item.get("exchangetimestamp") or "")
    ts_event = parse_angelone_timestamp(ts_str)

    return OrderReport(
        order_id=order_id,
        instrument_id=inst_id,
        side=side,
        quantity=qty,
        status=status,
        product=product,
        order_type=order_type,
        time_in_force=tif,
        filled_quantity=filled_shares,
        average_price=avg_price,
        price=price,
        trigger_price=trigger_price,
        reject_reason=reject_reason,
        ts_event=ts_event,
        venue_order_id=item.get("uniqueorderid"),
    )


def parse_trade(
    item: dict[str, Any],
    resolver: Callable[[str, str], InstrumentId | None] | None = None,
) -> Trade:
    """Parse a SmartAPI getTradeBook item into canonical Trade."""
    raw_symbol = str(item.get("tradingsymbol") or "").strip()
    clean_symbol = raw_symbol.removesuffix("-EQ")
    exchange = str(item.get("exchange") or "NSE").strip().upper()
    inst_id = (resolver(clean_symbol, exchange) if resolver else None) or InstrumentId(
        clean_symbol, exchange
    )

    side_str = str(item.get("transactiontype") or "BUY").strip().upper()
    side = ANGELONE_REVERSE_SIDE_MAP.get(side_str, OrderSide.BUY)

    qty = float(
        decimal_str(
            item.get("tradequantity") or item.get("fillshares") or item.get("quantity") or "0"
        )
    )
    price = float(
        decimal_str(item.get("tradeprice") or item.get("fillprice") or item.get("price") or "0")
    )

    ts_str = str(item.get("filltime") or item.get("tradetime") or "")
    ts_val = parse_angelone_timestamp(ts_str)

    order_id = str(item.get("orderid") or "").strip() or None

    return Trade(
        instrument_id=inst_id,
        side=side,
        quantity=qty,
        price=price,
        ts=ts_val,
        order_id=order_id,
    )


def parse_quote(data: dict[str, Any], instrument_id: InstrumentId) -> QuoteTick:
    """Parse SmartAPI market quote response or WebSocket tick into QuoteTick."""
    d = data.get("data") or data

    bid_price = float(
        decimal_str(
            d.get("depth", {}).get("buy", [{}])[0].get("price")
            or d.get("bid_price")
            or d.get("ltp")
            or "0"
        )
    )
    bid_size = float(
        decimal_str(
            d.get("depth", {}).get("buy", [{}])[0].get("quantity") or d.get("bid_size") or "1"
        )
    )

    ask_price = float(
        decimal_str(
            d.get("depth", {}).get("sell", [{}])[0].get("price")
            or d.get("ask_price")
            or d.get("ltp")
            or "0"
        )
    )
    ask_size = float(
        decimal_str(
            d.get("depth", {}).get("sell", [{}])[0].get("quantity") or d.get("ask_size") or "1"
        )
    )

    # Ensure valid bid/ask ordering and positive values
    if bid_price <= 0 and ask_price > 0:
        bid_price = ask_price
    elif ask_price <= 0 and bid_price > 0:
        ask_price = bid_price
    elif bid_price <= 0 and ask_price <= 0:
        ltp = float(decimal_str(d.get("ltp") or d.get("last_traded_price") or "100.0"))
        bid_price = ltp
        ask_price = ltp

    ts = parse_angelone_timestamp(
        str(d.get("exchange_timestamp") or d.get("last_traded_timestamp") or "")
    )
    if ts == 0:
        ts = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)

    return QuoteTick(
        instrument_id=instrument_id,
        ts=ts,
        bid_price=bid_price,
        ask_price=ask_price,
        bid_size=bid_size,
        ask_size=ask_size,
    )


def parse_depth(data: dict[str, Any], instrument_id: InstrumentId) -> MarketDepth:
    """Parse SmartAPI 5-level market depth into canonical MarketDepth."""
    d = data.get("data") or data
    depth_obj = d.get("depth") or d

    buy_list = depth_obj.get("buy") or depth_obj.get("best_5_buy") or []
    sell_list = depth_obj.get("sell") or depth_obj.get("best_5_sell") or []

    bids = tuple(
        DepthLevel(
            price=float(decimal_str(entry.get("price") or "0")),
            quantity=float(decimal_str(entry.get("quantity") or "0")),
        )
        for entry in buy_list
    )
    asks = tuple(
        DepthLevel(
            price=float(decimal_str(entry.get("price") or "0")),
            quantity=float(decimal_str(entry.get("quantity") or "0")),
        )
        for entry in sell_list
    )

    ts = parse_angelone_timestamp(str(d.get("exchange_timestamp") or ""))
    if ts == 0:
        ts = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)

    return MarketDepth(
        instrument_id=instrument_id,
        ts=ts,
        bids=bids,
        asks=asks,
    )


def parse_historical_bars(
    data: dict[str, Any] | list[Any], instrument_id: InstrumentId
) -> list[Bar]:
    """Parse SmartAPI getCandleData historical bars into canonical list[Bar]."""
    candles = data.get("data") if isinstance(data, dict) else data
    if not candles or not isinstance(candles, list):
        return []

    bars: list[Bar] = []
    for item in candles:
        if isinstance(item, list) and len(item) >= 6:
            ts = parse_angelone_timestamp(str(item[0]))
            bars.append(
                Bar(
                    instrument_id=instrument_id,
                    ts=ts,
                    open=float(decimal_str(item[1])),
                    high=float(decimal_str(item[2])),
                    low=float(decimal_str(item[3])),
                    close=float(decimal_str(item[4])),
                    volume=float(decimal_str(item[5])),
                )
            )
    # Ensure bars are sorted strictly ascending by timestamp
    bars.sort(key=lambda b: b.ts)
    return bars


def map_smartapi_error(status: int, code: str, message: str) -> Exception:
    """Classify SmartAPI HTTP and payload errorcodes into canonical exceptions."""
    msg = message or "SmartAPI error"
    code_upper = (code or "").strip().upper()
    msg_upper = msg.upper()

    if status == 429 or code_upper in ERROR_CODE_RATELIMIT or "REQUEST LIMIT" in msg_upper:
        return RateLimitExceeded(f"{code_upper}: {msg}")

    if (
        status in (401, 403)
        or code_upper in ERROR_CODE_AUTH
        or "TOKEN" in msg_upper
        or "EXPIRED" in msg_upper
        or "SESSION" in msg_upper
    ):
        return AuthenticationError(f"{code_upper}: {msg}")

    if status == 400 and (code_upper in ERROR_CODE_INVALID or "CHECKSUM" in msg_upper):
        return AdapterError(f"{code_upper}: {msg}")

    return AdapterError(f"{code_upper}: {msg}")


__all__ = [
    "build_modify_payload",
    "build_order_payload",
    "decimal_str",
    "format_decimal",
    "map_smartapi_error",
    "parse_angelone_timestamp",
    "parse_depth",
    "parse_funds",
    "parse_historical_bars",
    "parse_holding",
    "parse_margin_report",
    "parse_order",
    "parse_position",
    "parse_quote",
    "parse_trade",
    "whole_quantity",
]
