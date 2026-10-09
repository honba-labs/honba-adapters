"""Contract certification suite for DhanAdapter (M2).

Runs the full Honba adapter contract suite against DhanAdapter with mock HTTP transport.
Verifies descriptor, lifecycle, capability enforcement, market data, and execution round-tripping.
"""

from __future__ import annotations

import pytest
from honba.adapters.contract import verify_adapter_contract

from honba_dhan import DhanAdapter, DhanConfig, make_dhan_mock_transport


class TestDhanContract:
    @pytest.mark.asyncio
    async def test_dhan_adapter_satisfies_contract(self) -> None:
        config = DhanConfig.paper()
        mock_transport = make_dhan_mock_transport()
        adapter = DhanAdapter(config, http_transport=mock_transport)

        # Runs the strict canonical contract verification suite from honba.adapters.contract
        await verify_adapter_contract(adapter)
