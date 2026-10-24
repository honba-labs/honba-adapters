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

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import tomllib
from honba.adapters import Adapter, resolve_adapter
from honba.adapters.contract import verify_adapter_contract

from honba_adapters.common import recorded_transport
from honba_adapters.dhan import DhanAdapter

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _resolve(ref: str) -> Any:
    module, _, attr = ref.partition(":")
    return getattr(importlib.import_module(module), attr)


def _discover() -> list[str]:
    adapters_dir = ROOT / "src" / "honba_adapters"
    if adapters_dir.is_dir():
        return sorted(
            p.name
            for p in adapters_dir.iterdir()
            if p.is_dir() and p.name not in {"common", "__pycache__"}
        )
    return ["dhan", "zerodha"]


def _routes_file(name: str) -> Path:
    return FIXTURES / name / "routes.toml"


def _transport(name: str) -> httpx.MockTransport:
    spec = tomllib.loads(_routes_file(name).read_text())
    routes = {frag: f"{name}/{fixture}" for frag, fixture in spec["routes"].items()}
    return recorded_transport(routes, fixtures=FIXTURES, fallback=_resolve(spec["fallback"])())


def _build(name: str) -> Adapter:
    spec = tomllib.loads(_routes_file(name).read_text())
    adapter_cls: Callable[..., Adapter] = _resolve(spec["adapter"])
    return adapter_cls(_resolve(spec["config"]).paper(), http_transport=_transport(name))


def _declares_entry_point(name: str) -> bool:
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text()
        return f"{name} =" in text and '[project.entry-points."honba.adapters"]' in text
    return False


def _param(name: str) -> Any:
    if _routes_file(name).is_file():
        return pytest.param(name, id=name)
    if _declares_entry_point(name):
        return pytest.param(name, id=name)
    return pytest.param(
        name,
        id=name,
        marks=pytest.mark.skip(
            reason=f"{name}: scaffold, not certified (no tests/fixtures/{name}/routes.toml)"
        ),
    )


@pytest.mark.parametrize("broker", [_param(n) for n in _discover()])
async def test_contract_suite(broker: str) -> None:
    assert _routes_file(broker).is_file(), (
        f"{broker} declares a honba.adapters entry point but has no "
        f"tests/fixtures/{broker}/routes.toml; add it to certify the adapter"
    )
    await verify_adapter_contract(_build(broker))


async def test_adapter_wiring_recorded_fixture() -> None:
    """The registry wires a real adapter and its reads come from the recorded response."""
    adapter = resolve_adapter("dhan", mode="paper", http_transport=_transport("dhan"))
    assert isinstance(adapter, DhanAdapter)

    await adapter.connect()
    try:
        funds = await adapter.funds()
    finally:
        await adapter.disconnect()

    assert funds.available_cash == pytest.approx(1_234_567.0)

    await verify_adapter_contract(
        resolve_adapter("dhan", mode="paper", http_transport=_transport("dhan"))
    )
