"""Read-only smoke tests against the real Dhan API. Never places, modifies or cancels orders.

Skipped unless DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are set. Run with:
    pytest -m live dhan/tests/live
"""

from __future__ import annotations

import os

import pytest

from honba_adapters.dhan import DhanAdapter, DhanConfig

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("DHAN_CLIENT_ID") and os.environ.get("DHAN_ACCESS_TOKEN")),
        reason="DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set",
    ),
]


@pytest.fixture
async def adapter(install_readonly_guard, tmp_path):
    config = DhanConfig.live(
        os.environ["DHAN_CLIENT_ID"],
        os.environ["DHAN_ACCESS_TOKEN"],
        cache_dir=tmp_path,
    )
    a = DhanAdapter(config)
    install_readonly_guard(a)  # before connect(): nothing can ever reach an order endpoint
    await a.connect()
    yield a
    await a.disconnect()


async def test_connect_validates_token(adapter) -> None:
    assert adapter.is_connected()
    assert (await adapter.session()).user_id == os.environ["DHAN_CLIENT_ID"]


async def test_funds(adapter) -> None:
    assert await adapter.funds() is not None


async def test_positions_holdings_orders_trades_are_readable(adapter) -> None:
    for call in (adapter.positions, adapter.holdings, adapter.orders, adapter.trades):
        assert isinstance(await call(), list)


async def test_order_placement_is_impossible(adapter) -> None:
    with pytest.raises(AssertionError, match="read-only guard"):
        await adapter.place_order()
    with pytest.raises(AssertionError, match="read-only guard"):
        await adapter._http.post("/orders", json_body={})
