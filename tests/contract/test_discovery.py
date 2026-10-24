"""Tests for adapter dynamic entry-point discovery and registration."""

from __future__ import annotations

import pytest
from honba.adapters import (
    AdapterNotFound,
    available_adapters,
    resolve_adapter,
)

from honba_adapters.dhan import DhanAdapter
from honba_adapters.zerodha import ZerodhaAdapter


def test_registered_adapters_discovered() -> None:
    installed = available_adapters()
    # At least dhan and zerodha should be resolvable
    assert "dhan" in installed
    assert "zerodha" in installed


def test_resolve_dhan_adapter() -> None:
    adapter = resolve_adapter("dhan", mode="paper")
    assert isinstance(adapter, DhanAdapter)
    assert adapter.name == "dhan"
    assert adapter.is_connected() is False


def test_resolve_zerodha_adapter() -> None:
    adapter = resolve_adapter("zerodha", mode="paper")
    assert isinstance(adapter, ZerodhaAdapter)
    assert adapter.name == "zerodha"
    assert adapter.is_connected() is False


def test_resolve_unregistered_adapter_raises() -> None:
    with pytest.raises(AdapterNotFound):
        resolve_adapter("nonexistent_broker_xyz")
