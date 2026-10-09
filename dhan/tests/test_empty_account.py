"""Dhan answers HTTP 500 "No holdings available" for an empty demat account; that is not an error."""

from __future__ import annotations

import httpx
import pytest
from honba.adapters.errors import AdapterError

from honba_dhan import DhanAdapter, DhanConfig


def _adapter(status: int, body: dict) -> DhanAdapter:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    return DhanAdapter(DhanConfig.paper(), http_transport=transport)


_NO_HOLDINGS = {
    "errorType": "Data_Error",
    "errorCode": "DH-1111",
    "errorMessage": "No holdings available",
}


class TestEmptyHoldings:
    async def test_no_holdings_is_an_empty_list(self) -> None:
        adapter = _adapter(500, _NO_HOLDINGS)
        await adapter.connect()
        assert await adapter.holdings() == []

    async def test_other_server_errors_still_raise(self) -> None:
        adapter = _adapter(500, {"errorCode": "DH-902", "errorMessage": "Invalid access"})
        await adapter.connect()
        with pytest.raises(AdapterError):
            await adapter.holdings()

    async def test_no_holdings_does_not_hide_errors_on_other_endpoints(self) -> None:
        adapter = _adapter(500, _NO_HOLDINGS)
        await adapter.connect()
        with pytest.raises(AdapterError):
            await adapter.positions()
