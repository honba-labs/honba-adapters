"""Tests verifying entry-point registration and discovery with honba adapter registry (M5)."""

from __future__ import annotations

from honba.adapters import (
    Adapter,
    ExecutionAdapter,
    MarketDataAdapter,
    RunMode,
    available_adapters,
    default_registry,
    resolve_adapter,
    resolve_execution_adapter,
    resolve_market_data_adapter,
)
from honba_dhan import DhanAdapter
from honba_zerodha import ZerodhaAdapter


class TestAdapterRegistryDiscovery:
    def test_entry_points_are_discovered(self) -> None:
        adapters = available_adapters()
        assert "dhan" in adapters
        assert "zerodha" in adapters

    def test_resolve_dhan_adapter(self) -> None:
        adapter = resolve_adapter("dhan")
        assert isinstance(adapter, DhanAdapter)
        assert isinstance(adapter, Adapter)
        assert isinstance(adapter, ExecutionAdapter)
        assert isinstance(adapter, MarketDataAdapter)
        assert adapter.capabilities().name == "dhan"

    def test_resolve_zerodha_adapter(self) -> None:
        adapter = resolve_adapter("zerodha")
        assert isinstance(adapter, ZerodhaAdapter)
        assert isinstance(adapter, Adapter)
        assert isinstance(adapter, ExecutionAdapter)
        assert isinstance(adapter, MarketDataAdapter)
        assert adapter.capabilities().name == "zerodha"

    def test_resolve_execution_and_market_data(self) -> None:
        exec_dhan = resolve_execution_adapter("dhan")
        assert isinstance(exec_dhan, DhanAdapter)
        md_zerodha = resolve_market_data_adapter("zerodha")
        assert isinstance(md_zerodha, ZerodhaAdapter)

    def test_resolve_with_custom_mode_and_credentials(self) -> None:
        dhan = default_registry().create(
            "dhan",
            mode="live",
            client_id="custom_client",
            access_token="custom_token",
        )
        assert isinstance(dhan, DhanAdapter)
        assert dhan._config.client_id == "custom_client"
        assert dhan._config.mode is RunMode.LIVE

        zerodha = default_registry().create(
            "zerodha",
            mode="live",
            api_key="custom_key",
            access_token="custom_token",
        )
        assert isinstance(zerodha, ZerodhaAdapter)
        assert zerodha._config.api_key == "custom_key"
        assert zerodha._config.mode is RunMode.LIVE
