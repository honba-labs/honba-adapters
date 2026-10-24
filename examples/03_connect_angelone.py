#!/usr/bin/env python3
"""Example: Connect to Angel One SmartAPI 2.0 (Paper or Live)."""

from __future__ import annotations

import asyncio

from honba.adapters import resolve_adapter
from honba.adapters.contract import ExecutionAdapter


async def main() -> None:
    print("Resolving Angel One adapter dynamically via honba.adapters...")
    adapter = resolve_adapter("angelone", mode="paper")

    print(f"Adapter: {adapter.name}")
    print(f"Capabilities: {adapter.capabilities().features}")

    session = await adapter.connect()
    print(f"Connected session: user_id={session.user_id}, mode={session.mode.value}")

    try:
        if isinstance(adapter, ExecutionAdapter):
            funds = await adapter.funds()
            print(f"Funds available cash: ₹{funds.available_cash:,.2f}")
    finally:
        await adapter.disconnect()
        print("Disconnected cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
