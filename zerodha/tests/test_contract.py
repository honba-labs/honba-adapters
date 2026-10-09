"""Contract certification suite for ZerodhaAdapter (M3).

Runs the full Honba adapter contract suite against ZerodhaAdapter with mock HTTP transport.
Verifies descriptor, lifecycle, capability enforcement, market data, and execution round-tripping.
"""

from __future__ import annotations

import pytest
from honba.adapters.contract import verify_adapter_contract

from honba_zerodha import ZerodhaAdapter, ZerodhaConfig, make_zerodha_mock_transport


class TestZerodhaContract:
    @pytest.mark.asyncio
    async def test_zerodha_adapter_satisfies_contract(self) -> None:
        config = ZerodhaConfig.paper()
        mock_transport = make_zerodha_mock_transport()
        adapter = ZerodhaAdapter(config, http_transport=mock_transport)

        # Runs the strict canonical contract verification suite from honba.adapters.contract
        await verify_adapter_contract(adapter)
