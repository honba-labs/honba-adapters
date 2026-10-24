"""Contract certification suite for AngelOneAdapter (ADR 0010).

Runs the canonical Honba adapter contract suite against AngelOneAdapter with mock HTTP transport.
Verifies descriptor, lifecycle, capability enforcement, market data, and execution round-tripping.
"""

from __future__ import annotations

import pytest
from honba.adapters.contract import verify_adapter_contract

from honba_adapters.angelone import (
    AngelOneAdapter,
    AngelOneConfig,
    make_angelone_mock_transport,
)
from honba_adapters.common import assert_adapter_contract


class TestAngelOneContract:
    @pytest.mark.asyncio
    async def test_angelone_adapter_satisfies_contract(self) -> None:
        config = AngelOneConfig.paper()
        mock_transport = make_angelone_mock_transport()
        adapter = AngelOneAdapter(config, http_transport=mock_transport)

        # Runs the strict canonical contract verification suite from honba.adapters.contract
        await verify_adapter_contract(adapter)

    @pytest.mark.asyncio
    async def test_angelone_adapter_passes_hardened_contract_assert(self) -> None:
        config = AngelOneConfig.paper()
        mock_transport = make_angelone_mock_transport()
        adapter = AngelOneAdapter(config, http_transport=mock_transport)

        await assert_adapter_contract(adapter)
