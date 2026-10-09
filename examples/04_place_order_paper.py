#!/usr/bin/env python3
"""basic/04_place_order_paper: intent -> report, fills, a cancel, a reject, the books."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters import (
    OrderReport,
    Product,
    available_adapters,
    register_adapter,
    resolve_adapter,
)
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent
from honba.domain.trade import Trade

# Ensure reference fake is registered for testing and offline development
if "fake" not in available_adapters():
    try:
        register_adapter("fake", FakeAdapter)
    except Exception:  # noqa: BLE001, S110
        pass


def serialize_intent(intent: OrderIntent) -> dict[str, Any]:
    return {
        "instrument_id": {
            "symbol": intent.instrument_id.symbol,
            "exchange": intent.instrument_id.exchange,
        },
        "side": intent.side.value,
        "quantity": intent.quantity,
        "order_type": intent.order_type.value,
        "price": intent.price,
    }


def serialize_report(report: OrderReport) -> dict[str, Any]:
    return {
        "order_id": report.order_id,
        "status": report.status.value,
        "product": report.product.value if report.product else None,
        "quantity": report.quantity,
        "filled_quantity": report.filled_quantity,
        "average_price": report.average_price,
        "price": report.price,
        "reject_reason": report.reject_reason,
        "ts_event": report.ts_event,
    }


def serialize_trade(trade: Trade) -> dict[str, Any]:
    return {
        "order_id": trade.order_id,
        "instrument_id": {
            "symbol": trade.instrument_id.symbol,
            "exchange": trade.instrument_id.exchange,
        },
        "side": trade.side.value,
        "quantity": trade.quantity,
        "price": trade.price,
    }


async def _run_async(adapter: str = "fake", **config: Any) -> dict[str, Any]:
    inst = resolve_adapter(adapter, **config)
    await inst.connect()

    try:
        reliance = InstrumentId("RELIANCE", "NSE")
        tcs = InstrumentId("TCS", "NSE")

        # 1. Market buy that fills at the ask
        market_intent = OrderIntent.market_buy(reliance, 10.0)
        fill = await inst.place_order(
            market_intent, product=Product.DELIVERY, client_order_id="reliance-buy"
        )

        # 2. Idempotent repeat with same client_order_id
        repeat_fill = await inst.place_order(
            market_intent, product=Product.DELIVERY, client_order_id="reliance-buy"
        )
        orders_snapshot = await inst.orders()
        trades_snapshot = await inst.trades()
        idempotent_repeat = {
            "client_order_id": "reliance-buy",
            "same_report": repeat_fill == fill,
            "order_count": len(orders_snapshot),
            "trade_count": len(trades_snapshot),
        }

        # 3. Non-marketable limit rests then cancels
        limit_intent = OrderIntent.limit_buy(reliance, 1.0, 2400.0)
        accepted = await inst.place_order(
            limit_intent, product=Product.DELIVERY, client_order_id="reliance-limit"
        )
        await inst.cancel_order(accepted.order_id)
        cancelled = await inst.order_status(accepted.order_id)

        # 4. Oversized buy rejected as data, not an exception
        rejected_intent = OrderIntent.market_buy(tcs, 1000.0)
        rejected = await inst.place_order(
            rejected_intent, product=Product.DELIVERY, client_order_id="tcs-oversized"
        )

        # 5. Orders, trades, funds
        orders = await inst.orders()
        trades = await inst.trades()
        funds = await inst.funds()

        return {
            "adapter": adapter,
            "product": "delivery",
            "market_buy": {
                "intent": serialize_intent(market_intent),
                "report": serialize_report(fill),
            },
            "idempotent_repeat": idempotent_repeat,
            "resting_limit": {
                "intent": serialize_intent(limit_intent),
                "accepted": serialize_report(accepted),
                "cancelled": serialize_report(cancelled),
            },
            "rejected_buy": {
                "intent": serialize_intent(rejected_intent),
                "report": serialize_report(rejected),
            },
            "orders": [serialize_report(o) for o in orders],
            "trades": [serialize_trade(t) for t in trades],
            "funds": {
                "available_cash": funds.available_cash,
                "opening_balance": funds.opening_balance,
                "currency": funds.currency,
                "margin_used": funds.margin_used,
            },
        }
    finally:
        await inst.disconnect()


def run(adapter: str = "fake", **config: Any) -> dict[str, Any]:
    """Execute a paper order sequence and return the intents, reports, and books."""
    return asyncio.run(_run_async(adapter, **config))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Place a sequence of paper orders and record the lifecycle results."
    )
    parser.add_argument(
        "--adapter",
        default="fake",
        help="Adapter name (default: fake)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to write the JSON result to.",
    )

    args = parser.parse_args(argv)
    result = run(adapter=args.adapter)

    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
