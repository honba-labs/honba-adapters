"""SemVer stability, entry-point discovery, and cross-repo contract gate tests."""

from __future__ import annotations

import pytest
from honba.adapters import (
    Adapter,
    AdapterCapabilities,
    ExecutionAdapter,
    MarketDataAdapter,
    available_adapters,
    resolve_adapter,
)

EXPECTED_ADAPTERS = ("dhan", "zerodha", "angelone")


class TestSemVerAndDiscoveryGate:
    def test_all_expected_adapters_are_discoverable(self) -> None:
        discovered = set(available_adapters())
        for expected in EXPECTED_ADAPTERS:
            assert expected in discovered, (
                f"Adapter '{expected}' is missing from available_adapters()"
            )

    @pytest.mark.parametrize("name", EXPECTED_ADAPTERS)
    def test_adapter_dynamic_resolution_and_role_conformance(self, name: str) -> None:
        adapter = resolve_adapter(name, mode="paper")
        assert isinstance(adapter, Adapter)
        assert isinstance(adapter, MarketDataAdapter)
        assert isinstance(adapter, ExecutionAdapter)

        assert adapter.name == name
        assert not adapter.is_connected()

        caps = adapter.capabilities()
        assert isinstance(caps, AdapterCapabilities)
        assert caps.name == name
        assert len(caps.exchanges) > 0
        assert len(caps.features) > 0
        assert len(caps.products) > 0
        assert len(caps.order_types) > 0
        assert len(caps.time_in_force) > 0

    @pytest.mark.parametrize("name", EXPECTED_ADAPTERS)
    def test_version_exports(self, name: str) -> None:
        import honba_adapters

        assert hasattr(honba_adapters, "__version__")
        assert isinstance(honba_adapters.__version__, str)
        assert len(honba_adapters.__version__.split(".")) >= 3
