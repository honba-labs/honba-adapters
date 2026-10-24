"""Execution flow tests for AngelOneAdapter with spy and mock transports."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from honba.adapters.errors import CapabilityError
from honba.adapters.models import Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderStatus

from honba_adapters.angelone import (
    AngelOneAdapter,
    AngelOneConfig,
    make_angelone_mock_transport,
)

RELIANCE = InstrumentId("RELIANCE", "NSE")


class AngelSpyTransport(httpx.AsyncBaseTransport):
    """Spy transport logging requests and injecting simulated responses."""

    def __init__(self) -> None:
        self.inner = make_angelone_mock_transport()
        self.requests: list[httpx.Request] = []
        self.post_rejection: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and "placeOrder" in request.url.path and self.post_rejection:
            return httpx.Response(
                400,
                json={
                    "status": False,
                    "message": self.post_rejection,
                    "errorcode": "AG8001",
                },
            )
        return await self.inner.handle_async_request(request)

    def last_json(self) -> dict[str, Any]:
        posts = [r for r in self.requests if r.method == "POST"]
        return json.loads(posts[-1].content.decode("utf-8"))


@pytest.fixture
async def adapter_spy() -> tuple[AngelOneAdapter, AngelSpyTransport]:
    spy = AngelSpyTransport()
    adapter = AngelOneAdapter(AngelOneConfig.paper(), http_transport=spy)
    await adapter.connect()
    return adapter, spy


class TestAngelOneExecutionFlow:
    @pytest.mark.asyncio
    async def test_place_market_order_and_fill(
        self, adapter_spy: tuple[AngelOneAdapter, AngelSpyTransport]
    ) -> None:
        adapter, spy = adapter_spy
        intent = OrderIntent.market_buy(RELIANCE, 5.0)
        report = await adapter.place_order(
            intent, product=Product.DELIVERY, client_order_id="CID-101"
        )

        assert report.order_id
        assert report.status is OrderStatus.ACCEPTED
        assert report.filled_quantity == 0.0

        body = spy.last_json()
        assert body["tradingsymbol"] == "RELIANCE-EQ"
        assert body["transactiontype"] == "BUY"
        assert body["ordertype"] == "MARKET"
        assert body["quantity"] == "5"

    @pytest.mark.asyncio
    async def test_place_limit_order_resting_then_cancel(
        self, adapter_spy: tuple[AngelOneAdapter, AngelSpyTransport]
    ) -> None:
        adapter, _ = adapter_spy
        intent = OrderIntent.limit_buy(RELIANCE, 2.0, price=2000.0)
        report = await adapter.place_order(intent, product=Product.DELIVERY)

        assert report.status is OrderStatus.ACCEPTED
        assert report.price == 2000.0

        # Modify
        modified = await adapter.modify_order(report.order_id, price=2050.0)
        assert modified.price == 2050.0

        # Cancel
        await adapter.cancel_order(report.order_id)
        status = await adapter.order_status(report.order_id)
        assert status.status is OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_idempotent_client_order_id(
        self, adapter_spy: tuple[AngelOneAdapter, AngelSpyTransport]
    ) -> None:
        adapter, _ = adapter_spy
        intent = OrderIntent.market_buy(RELIANCE, 1.0)
        first = await adapter.place_order(
            intent, product=Product.INTRADAY, client_order_id="CL-UNIQUE-1"
        )
        second = await adapter.place_order(
            intent, product=Product.INTRADAY, client_order_id="CL-UNIQUE-1"
        )

        assert first.order_id == second.order_id

    @pytest.mark.asyncio
    async def test_broker_rejection_returns_rejected_report_never_exception(
        self, adapter_spy: tuple[AngelOneAdapter, AngelSpyTransport]
    ) -> None:
        adapter, spy = adapter_spy
        spy.post_rejection = "Margin shortfall: required 100000, available 1000"

        intent = OrderIntent.market_buy(RELIANCE, 100.0)
        report = await adapter.place_order(intent, product=Product.DELIVERY)

        assert report.status is OrderStatus.REJECTED
        assert "Margin shortfall" in (report.reject_reason or "")

    @pytest.mark.asyncio
    async def test_undeclared_trailing_stop_raises_capability_error(
        self, adapter_spy: tuple[AngelOneAdapter, AngelSpyTransport]
    ) -> None:
        adapter, spy = adapter_spy
        intent = OrderIntent.trailing_stop_sell(RELIANCE, 1.0, trail_percent=1.0)

        with pytest.raises(CapabilityError, match="not supported"):
            await adapter.place_order(intent, product=Product.DELIVERY)

        # Ensure no network request was sent
        assert not any("placeOrder" in r.url.path for r in spy.requests)
