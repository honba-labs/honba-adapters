#!/usr/bin/env python3
"""basic/01_connect_dhan: connect an adapter through the registry (default fake, offline)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters import (
    AdapterNotFound,
    available_adapters,
    register_adapter,
    resolve_adapter,
)
from honba.adapters.testing import FakeAdapter

# Ensure reference fake is registered for testing and offline development
if "fake" not in available_adapters():
    try:
        register_adapter("fake", FakeAdapter)
    except Exception:  # noqa: BLE001, S110
        pass


async def _run_async(adapter: str = "fake", **config: Any) -> dict[str, Any]:
    inst = resolve_adapter(adapter, **config)
    session = await inst.connect()
    caps = inst.capabilities()
    try:
        return {
            "connected": inst.is_connected(),
            "adapter": adapter,
            "session": {
                "user_id": session.user_id,
                "mode": session.mode.value,
                "accounts": list(session.accounts),
                "expires_at": session.expires_at,
            },
            "capabilities": {
                "name": caps.name,
                "exchanges": sorted(caps.exchanges),
                "order_types": sorted(t.value for t in caps.order_types),
                "products": sorted(p.value for p in caps.products),
                "features": sorted(f.value for f in caps.features),
                "time_in_force": sorted(tif.value for tif in caps.time_in_force),
                "stream_modes": sorted(sm.value for sm in caps.stream_modes),
            },
        }
    finally:
        await inst.disconnect()


def run(adapter: str = "fake", **config: Any) -> dict[str, Any]:
    """Connect to the named adapter, retrieve its session and capabilities, and disconnect."""
    return asyncio.run(_run_async(adapter, **config))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Connect an adapter through the registry and inspect session and capabilities."
    )
    parser.add_argument(
        "--adapter",
        default="fake",
        help="Adapter name to connect to (default: fake)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to write the JSON result to.",
    )

    args = parser.parse_args(argv)

    try:
        result = run(adapter=args.adapter)
    except AdapterNotFound as err:
        sys.exit(str(err))

    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
