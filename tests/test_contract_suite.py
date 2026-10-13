"""The shared contract suite, run against every certified adapter on recorded fixtures (E1-S1b).

Two things this proves that the per-adapter suites do not:

* ``test_contract_suite[dhan]`` / ``test_contract_suite[zerodha]`` run the one canonical
  ``honba.adapters.contract`` suite over *both* real adapters from a single parametrisation,
  so "a second adapter passes the contract" is checkable in one place.
* ``test_adapter_wiring_recorded_fixture`` resolves an adapter through the registry and drives
  its account/market reads from broker responses recorded in ``tests/fixtures/``, so the
  certification does not depend on hand-written inline bodies (the recorded value is asserted,
  which the in-memory fallback would not produce).

The order lifecycle stays on each adapter's in-memory paper transport (the ``fallback``): it is
stateful (place -> read back -> cancel -> idempotent re-place) and cannot be replayed from a
single recorded read. The recorded routes cover the read-only account and market-data surface.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from honba.adapters import Adapter, resolve_adapter
from honba.adapters.contract import verify_adapter_contract
from honba_adapters_shared import recorded_transport
from honba_dhan import DhanAdapter, DhanConfig, make_dhan_mock_handler
from honba_zerodha import ZerodhaAdapter, ZerodhaConfig, make_zerodha_mock_handler

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Recorded read endpoints per adapter. ``recorded_transport`` matches by the longest path
#: fragment, so declaring the routes is enough; map order does not matter.
_DHAN_ROUTES = {
    "/fundlimit": "dhan/fund_limit.json",
    "/marketfeed/quote": "dhan/quote.json",
    "/charts/historical": "dhan/historical.json",
    "/charts/intraday": "dhan/historical.json",
    "/margincalculator": "dhan/margin.json",
    "/trades": "dhan/trades.json",
    "/positions": "dhan/positions.json",
    "/holdings": "dhan/holdings.json",
}

_ZERODHA_ROUTES = {
    "/user/margins": "zerodha/margins.json",
    "/margins/orders": "zerodha/order_margins.json",
    "/quote": "zerodha/quote.json",
    "/instruments/historical": "zerodha/historical.json",
    "/instruments": "zerodha/instruments.csv",
    "/trades": "zerodha/trades.json",
    "/portfolio/positions": "zerodha/positions.json",
    "/portfolio/holdings": "zerodha/holdings.json",
    "/session/token": "zerodha/token.json",
}


def _dhan_transport() -> httpx.MockTransport:
    return recorded_transport(
        _DHAN_ROUTES, fixtures=FIXTURES, fallback=make_dhan_mock_handler()
    )


def _dhan() -> DhanAdapter:
    return DhanAdapter(DhanConfig.paper(), http_transport=_dhan_transport())


def _zerodha_transport() -> httpx.MockTransport:
    return recorded_transport(
        _ZERODHA_ROUTES, fixtures=FIXTURES, fallback=make_zerodha_mock_handler()
    )


def _zerodha() -> ZerodhaAdapter:
    return ZerodhaAdapter(ZerodhaConfig.paper(), http_transport=_zerodha_transport())


#: Every adapter this repository certifies, by registry name.
_BUILDERS: dict[str, Callable[[], Adapter]] = {"dhan": _dhan, "zerodha": _zerodha}


@pytest.mark.parametrize("broker", sorted(_BUILDERS))
async def test_contract_suite(broker: str) -> None:
    await verify_adapter_contract(_BUILDERS[broker]())


async def test_adapter_wiring_recorded_fixture() -> None:
    """The registry wires a real adapter and its reads come from the recorded response."""
    adapter = resolve_adapter("dhan", mode="paper", http_transport=_dhan_transport())
    assert isinstance(adapter, DhanAdapter)

    await adapter.connect()
    try:
        funds = await adapter.funds()
    finally:
        await adapter.disconnect()

    # The recorded body, not the in-memory fallback (which reports 1_000_000.0).
    assert funds.available_cash == pytest.approx(1_234_567.0)

    # ...and the whole contract holds for a freshly wired adapter on the same recording.
    await verify_adapter_contract(
        resolve_adapter("dhan", mode="paper", http_transport=_dhan_transport())
    )
