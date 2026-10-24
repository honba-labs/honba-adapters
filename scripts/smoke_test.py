#!/usr/bin/env python3
"""Live and sandbox smoke test runner for Honba broker adapters (ADR 0010, M5).

Guarded with HONBA_LIVE_SMOKE_TEST=1 for live execution against real broker accounts
to prevent accidental orders or unexpected credential leaks.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from honba.adapters import (
    FullAdapterClient,
    Product,
    RunMode,
    available_adapters,
    resolve_full_adapter,
)
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderStatus


async def run_smoke_test(args: argparse.Namespace) -> int:
    adapter_name = args.adapter.lower()
    mode_str = args.mode.lower()
    is_live = mode_str == "live"

    print("=" * 65)
    print(f"HONBA BROKER ADAPTER SMOKE TEST: {adapter_name.upper()}")
    print(f"Mode: {mode_str.upper()}")
    print("=" * 65)

    if is_live:
        guard = os.environ.get("HONBA_LIVE_SMOKE_TEST", "").strip()
        if guard != "1":
            print(
                "[GUARD] Refusing to run in LIVE mode without HONBA_LIVE_SMOKE_TEST=1.",
                file=sys.stderr,
            )
            print(
                "        Set export HONBA_LIVE_SMOKE_TEST=1 to acknowledge live account access.",
                file=sys.stderr,
            )
            return 2

    # Verify adapter registration
    adapters = available_adapters()
    if adapter_name not in adapters:
        print(
            f"[ERROR] Adapter {adapter_name!r} not found. Installed: {', '.join(adapters)}",
            file=sys.stderr,
        )
        return 1

    # Build adapter configuration kwargs
    config_kwargs = {"mode": RunMode.LIVE if is_live else RunMode.PAPER}
    if args.client_id:
        config_kwargs["client_id"] = args.client_id
    if args.api_key:
        config_kwargs["api_key"] = args.api_key
    if args.access_token:
        config_kwargs["access_token"] = args.access_token

    print(f"1. Resolving adapter {adapter_name!r} via registry...")
    try:
        adapter: FullAdapterClient = resolve_full_adapter(adapter_name, **config_kwargs)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] Failed to instantiate adapter: {e}", file=sys.stderr)
        return 1

    caps = adapter.capabilities()
    print(f"   Capabilities: {caps.name}")
    print(f"   Exchanges: {sorted(caps.exchanges)}")
    print(f"   Supported features: {len(caps.features)} features enabled")

    print(f"\n2. Connecting to {adapter_name}...")
    try:
        session = await adapter.connect()
        print(f"   [OK] Connected. Session user: {session.user_id}, mode: {session.mode.value}")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] Connection failed: {e}", file=sys.stderr)
        return 1

    try:
        # 3. Funds & margins
        print("\n3. Fetching account funds and margins...")
        funds = await adapter.funds()
        print(f"   Available cash: {funds.available_cash:,.2f} {funds.currency}")
        print(f"   Margin used: {funds.margin_used:,.2f} {funds.currency}")

        # 4. Portfolio holdings & positions
        print("\n4. Fetching portfolio status...")
        holdings = await adapter.holdings()
        positions = await adapter.positions()
        print(f"   Holdings count: {len(holdings)}")
        print(f"   Positions count: {len(positions)}")

        # 5. Market data quotes & depth
        instrument_id = InstrumentId(args.symbol, args.exchange)
        print(f"\n5. Fetching market data snapshot for {instrument_id}...")
        quote = await adapter.quote(instrument_id)
        print(
            f"   Quote: bid={quote.bid_price} (size={quote.bid_size}), "
            f"ask={quote.ask_price} (size={quote.ask_size})"
        )

        depth = await adapter.depth(instrument_id)
        print(
            f"   Depth: top bid={depth.bids[0].price if depth.bids else 'N/A'}, "
            f"top ask={depth.asks[0].price if depth.asks else 'N/A'}"
        )

        # 6. Order lifecycle test
        run_order_test = (not is_live) or args.place_live_order
        if run_order_test:
            print(f"\n6. Testing order placement & cancellation for {instrument_id}...")
            # Use limit order far from market to avoid accidental fill
            test_price = (quote.bid_price * 0.90) if quote.bid_price > 0 else 100.0
            test_order_id = f"smoke-{uuid.uuid4().hex[:6]}"
            intent = OrderIntent.limit_buy(instrument_id, 1.0, test_price)
            report = await adapter.place_order(
                intent, product=Product.DELIVERY, client_order_id=test_order_id
            )
            print(f"   [OK] Placed order: {report.order_id}, status={report.status.value}")

            if report.status in (OrderStatus.ACCEPTED, OrderStatus.SUBMITTED):
                await adapter.cancel_order(report.order_id)
                post_cancel = await adapter.order_status(report.order_id)
                print(f"   [OK] Cancelled order: status={post_cancel.status.value}")
        else:
            print("\n6. Skipping order placement (live order placement not requested)")

        print("\n7. Disconnecting...")
        await adapter.disconnect()
        print("   [OK] Disconnected cleanly.")

    except Exception as e:  # noqa: BLE001
        print(f"\n[FAIL] Smoke test encounter error: {e}", file=sys.stderr)
        try:
            await adapter.disconnect()
        except Exception:  # noqa: BLE001, S110
            pass
        return 1

    print("\n" + "=" * 65)
    print(f"[SUCCESS] Smoke test completed successfully for {adapter_name.upper()}!")
    print("=" * 65 + "\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Honba Broker Adapter Smoke Test")
    parser.add_argument(
        "--adapter",
        choices=["dhan", "zerodha"],
        default="dhan",
        help="Broker adapter name (default: dhan)",
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Execution mode (default: paper)",
    )
    parser.add_argument("--symbol", default="RELIANCE", help="Symbol to query (default: RELIANCE)")
    parser.add_argument("--exchange", default="NSE", help="Exchange (default: NSE)")
    parser.add_argument("--client-id", default=None, help="Dhan Client ID")
    parser.add_argument("--api-key", default=None, help="Zerodha API Key")
    parser.add_argument("--access-token", default=None, help="Broker Access Token")
    parser.add_argument(
        "--place-live-order",
        action="store_true",
        help="Allow placing a test limit order in live mode (disabled by default)",
    )

    args = parser.parse_args()
    code = asyncio.run(run_smoke_test(args))
    sys.exit(code)


if __name__ == "__main__":
    main()
