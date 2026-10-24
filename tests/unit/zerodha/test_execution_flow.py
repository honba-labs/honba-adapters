"""Gateway semantics ported from honba-broker-zerodha ``gateway.rs`` golden tests.

Integration tests through the ``ZerodhaAdapter`` facade with the in-memory mock transport
(``honba_zerodha.mock``) wrapped by a spy that records requests and injects failures.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest
from honba.adapters.errors import AdapterError, CapabilityError
from honba.adapters.models import Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderStatus, OrderType, TimeInForce

from honba_adapters.common.errors import RateLimitExceeded
from honba_adapters.zerodha import ZerodhaAdapter, ZerodhaConfig, make_zerodha_mock_transport
from honba_adapters.zerodha.http import ZerodhaTransportError
from honba_adapters.zerodha.parsing import parse_kite_timestamp

INFY = InstrumentId("RELIANCE", "NSE")  # token 738561 is pre-seeded in the master


class SpyTransport(httpx.AsyncBaseTransport):
    """Delegates to the mock transport; can fail a POST before or after the venue saw it."""

    def __init__(self) -> None:
        self.inner = make_zerodha_mock_transport()
        self.requests: list[httpx.Request] = []
        self.post_failures: list[str] = []  # "before" | "after", consumed per place POST
        self.trades_override: Callable[[], list[dict[str, object]]] | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        is_place = request.method == "POST" and request.url.path.startswith("/orders/")
        if is_place and self.post_failures:
            mode = self.post_failures.pop(0)
            if mode == "after":
                await self.inner.handle_async_request(request)
            raise httpx.ReadTimeout("timed out", request=request)
        if self.trades_override is not None and request.url.path == "/trades":
            return httpx.Response(200, json={"status": "success", "data": self.trades_override()})
        return await self.inner.handle_async_request(request)

    def form(self, index: int) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.requests[index].content.decode()).items()}

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


async def make(spy: SpyTransport | None = None) -> tuple[ZerodhaAdapter, SpyTransport]:
    spy = spy or SpyTransport()
    adapter = ZerodhaAdapter(ZerodhaConfig.paper(), http_transport=spy)
    await adapter.connect()
    return adapter, spy


def limit_buy(qty: float = 10.0, tif: TimeInForce = TimeInForce.DAY) -> OrderIntent:
    return OrderIntent(INFY, OrderSide.BUY, qty, OrderType.LIMIT, price=1500.5, time_in_force=tif)


class TestRequestShape:
    async def test_validity_follows_intent_and_tag_is_kite_safe(self) -> None:
        adapter, spy = await make()
        await adapter.place_order(
            limit_buy(tif=TimeInForce.IOC), product=Product.INTRADAY, client_order_id="O-1"
        )
        form = spy.form(-1)
        assert form["validity"] == "IOC"
        assert form["tag"] == "O1"
        assert form["product"] == "MIS"
        assert form["price"] == "1500.5"
        assert form["quantity"] == "10"

    @pytest.mark.parametrize("tif", [TimeInForce.GTC, TimeInForce.FOK, TimeInForce.GTD])
    async def test_unsupported_validity_is_capability_error_and_sends_nothing(
        self, tif: TimeInForce
    ) -> None:
        adapter, spy = await make()
        with pytest.raises(CapabilityError):
            await adapter.place_order(limit_buy(tif=tif), product=Product.DELIVERY)
        assert spy.requests == []

    async def test_fractional_quantity_is_invalid_before_network(self) -> None:
        adapter, spy = await make()
        with pytest.raises(AdapterError, match="whole"):
            await adapter.place_order(limit_buy(qty=2.5), product=Product.DELIVERY)
        assert spy.requests == []

    async def test_modify_rejects_fractional_or_non_positive_quantity(self) -> None:
        adapter, spy = await make()
        report = await adapter.place_order(limit_buy(), product=Product.DELIVERY)
        n = len(spy.requests)
        for q in (0.0, -1.0, 2.5, float("nan")):
            with pytest.raises(AdapterError):
                await adapter.modify_order(report.order_id, quantity=q)
        assert len(spy.requests) == n

    async def test_non_positive_price_is_invalid_before_network(self) -> None:
        adapter, spy = await make()
        bad = OrderIntent(INFY, OrderSide.BUY, 1.0, OrderType.LIMIT, price=0.0)
        with pytest.raises(AdapterError, match="price"):
            await adapter.place_order(bad, product=Product.DELIVERY)
        assert spy.requests == []

    async def test_resubmitting_known_client_id_is_idempotent(self) -> None:
        adapter, spy = await make()
        a = await adapter.place_order(limit_buy(), product=Product.DELIVERY, client_order_id="X1")
        b = await adapter.place_order(limit_buy(), product=Product.DELIVERY, client_order_id="X1")
        assert a == b
        assert len(spy.posts()) == 1


class TestAmbiguousSubmit:
    async def test_failure_then_resubmit_adopts_existing_order_without_placing_again(
        self,
    ) -> None:
        spy = SpyTransport()
        adapter, _ = await make(spy)
        spy.post_failures.append("after")  # the venue saw it, the reply was lost
        with pytest.raises(ZerodhaTransportError):
            await adapter.place_order(limit_buy(), product=Product.DELIVERY, client_order_id="O-1")
        report = await adapter.place_order(
            limit_buy(), product=Product.DELIVERY, client_order_id="O-1"
        )
        assert len(spy.posts()) == 1, "no second place"
        gets = [r for r in spy.requests if r.method == "GET" and r.url.path == "/orders"]
        assert len(gets) == 1
        assert report.order_id == "KITE-ORD-1001"
        assert report.status is OrderStatus.ACCEPTED
        # The adopted order is registered: its fill maps back to it.
        fills = await adapter.poll_fills()
        assert [t.order_id for t in fills] == ["KITE-ORD-1001"]

    async def test_failure_then_resubmit_places_when_no_tagged_order_exists(self) -> None:
        spy = SpyTransport()
        adapter, _ = await make(spy)
        spy.post_failures.append("before")  # the venue never saw it
        with pytest.raises(ZerodhaTransportError):
            await adapter.place_order(limit_buy(), product=Product.DELIVERY, client_order_id="O-1")
        report = await adapter.place_order(
            limit_buy(), product=Product.DELIVERY, client_order_id="O-1"
        )
        assert report.order_id == "KITE-ORD-1001"
        assert len(spy.posts()) == 2
        assert [r.method for r in spy.requests[-3:]] == ["POST", "GET", "POST"]

    async def test_rate_limit_on_place_is_also_ambiguous(self) -> None:
        class Limited(SpyTransport):
            limited = False

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                if request.method == "POST" and not self.limited:
                    self.limited = True
                    self.requests.append(request)
                    return httpx.Response(429, json={"status": "error", "message": "slow"})
                return await super().handle_async_request(request)

        spy = Limited()
        adapter, _ = await make(spy)
        with pytest.raises(RateLimitExceeded):
            await adapter.place_order(limit_buy(), product=Product.DELIVERY, client_order_id="R1")
        await adapter.place_order(limit_buy(), product=Product.DELIVERY, client_order_id="R1")
        assert any(r.method == "GET" and r.url.path == "/orders" for r in spy.requests)

    async def test_rejection_is_not_ambiguous(self) -> None:
        adapter, spy = await make()
        report = await adapter.place_order(
            limit_buy(qty=1_000_000), product=Product.DELIVERY, client_order_id="BIG"
        )
        assert report.status is OrderStatus.REJECTED
        assert not [r for r in spy.requests if r.method == "GET" and r.url.path == "/orders"]
        assert len(spy.posts()) == 1


class TestFills:
    async def test_poll_fills_emits_known_trades_once(self) -> None:
        adapter, _ = await make()
        report = await adapter.place_order(limit_buy(), product=Product.DELIVERY)
        assert report.order_id == "KITE-ORD-1001"
        first = await adapter.poll_fills()
        assert len(first) == 1
        assert first[0].order_id == "KITE-ORD-1001"
        assert first[0].price == 2450.0 and first[0].side is OrderSide.BUY
        assert first[0].ts == parse_kite_timestamp("2024-01-02 09:15:30")
        assert await adapter.poll_fills() == []

    async def test_trades_of_unknown_orders_are_ignored(self) -> None:
        adapter, _ = await make()
        assert await adapter.poll_fills() == []

    async def test_poll_fills_picks_up_new_trades_on_later_polls(self) -> None:
        spy = SpyTransport()
        adapter, _ = await make(spy)
        await adapter.place_order(limit_buy(), product=Product.DELIVERY)
        base = {
            "order_id": "KITE-ORD-1001",
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "transaction_type": "BUY",
            "fill_timestamp": "2024-01-02 09:15:30",
        }
        rows = [{**base, "trade_id": "T1", "quantity": 1, "average_price": 10.0}]
        spy.trades_override = lambda: list(rows)
        assert len(await adapter.poll_fills()) == 1
        rows.append({**base, "trade_id": "T2", "quantity": 2, "average_price": 11.0})
        fills = await adapter.poll_fills()
        assert [f.quantity for f in fills] == [2.0]

    async def test_malformed_rows_are_skipped_not_emitted(self) -> None:
        spy = SpyTransport()
        adapter, _ = await make(spy)
        await adapter.place_order(limit_buy(), product=Product.DELIVERY)
        base = {
            "order_id": "KITE-ORD-1001",
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "average_price": 10.0,
            "fill_timestamp": "2024-01-02 09:15:30",
        }
        spy.trades_override = lambda: [
            {**base, "trade_id": "T1", "quantity": 0},
            {**base, "trade_id": "T2", "quantity": 1, "fill_timestamp": "garbage"},
        ]
        assert await adapter.poll_fills() == []

    async def test_missing_side_falls_back_to_the_known_order_side(self) -> None:
        spy = SpyTransport()
        adapter, _ = await make(spy)
        sell = OrderIntent(INFY, OrderSide.SELL, 1.0)
        await adapter.place_order(sell, product=Product.DELIVERY)
        spy.trades_override = lambda: [
            {
                "trade_id": "T1",
                "order_id": "KITE-ORD-1001",
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "quantity": 1,
                "average_price": 10.0,
                "fill_timestamp": "2024-01-02 09:15:30",
            }
        ]
        fills = await adapter.poll_fills()
        assert [f.side for f in fills] == [OrderSide.SELL]

    async def test_transport_errors_propagate(self) -> None:
        spy = SpyTransport()
        adapter, _ = await make(spy)

        def boom() -> list[dict[str, object]]:
            raise httpx.ReadTimeout("t")

        spy.trades_override = boom
        with pytest.raises(AdapterError):
            await adapter.poll_fills()


class TestSessionAndInstruments:
    async def test_generate_session_stores_token_and_returns_user(self) -> None:
        adapter = ZerodhaAdapter(
            ZerodhaConfig.paper(api_key="kitekey", access_token="old"),
            http_transport=make_zerodha_mock_transport(),
        )
        user = await adapter.generate_session("requesttoken", "secret")
        assert user == "AB1234"
        assert adapter._config.access_token == "newtok"
        assert adapter._config.user_id == "AB1234"

    async def test_generate_session_needs_a_secret(self) -> None:
        adapter = ZerodhaAdapter(
            ZerodhaConfig.paper(), http_transport=make_zerodha_mock_transport()
        )
        with pytest.raises(AdapterError, match="secret"):
            await adapter.generate_session("rt")

    async def test_refresh_instruments_loads_the_csv_dump(self) -> None:
        adapter, spy = await make()
        count = await adapter.refresh_instruments()
        assert count >= 2
        assert spy.requests[-1].url.path == "/instruments"
        assert adapter._instruments.resolve_token(InstrumentId("INFY", "NSE")) == 408065
        await adapter.refresh_instruments("NSE")
        assert spy.requests[-1].url.path == "/instruments/NSE"
