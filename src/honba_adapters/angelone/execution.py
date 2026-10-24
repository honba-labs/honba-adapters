"""ExecutionAdapter implementation for Angel One SmartAPI 2.0."""

from __future__ import annotations

import contextlib
import logging
import math
from typing import TYPE_CHECKING

from honba.adapters.errors import (
    AdapterError,
    CapabilityError,
)
from honba.adapters.models import Funds, Holding, MarginReport, OrderReport, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderStatus
from honba.domain.position import Position
from honba.domain.trade import Trade

from honba_adapters.angelone.constants import (
    ANGELONE_ORDER_TYPE_MAP,
    ANGELONE_PRODUCT_MAP,
    ANGELONE_TIF_MAP,
)
from honba_adapters.angelone.http import (
    AngelOneHttpClient,
    AngelOneRejectedError,
    AngelOneTransportError,
)
from honba_adapters.angelone.parsing import (
    build_modify_payload,
    build_order_payload,
    parse_funds,
    parse_holding,
    parse_margin_report,
    parse_order,
    parse_position,
    parse_trade,
    whole_quantity,
)

if TYPE_CHECKING:
    from honba_adapters.angelone.instruments import AngelOneInstrumentMaster

logger = logging.getLogger("honba.adapters.angelone.execution")

_LIVE_STATUSES = frozenset(
    {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.SUBMITTED}
)


class AngelOneExecutionMixin:
    """Implements ExecutionAdapter methods for AngelOneAdapter."""

    _http: AngelOneHttpClient
    _instruments: AngelOneInstrumentMaster
    _orders_by_id: dict[str, OrderReport]
    _orders_by_client_id: dict[str, OrderReport]
    _ambiguous: set[str]

    def _id_resolver(self, symbol: str, exchange: str) -> InstrumentId | None:
        inst = InstrumentId(symbol, exchange)
        if self._instruments.resolve_token(inst) is not None:
            return inst
        return None

    async def place_order(
        self,
        intent: OrderIntent,
        *,
        product: Product,
        client_order_id: str | None = None,
    ) -> OrderReport:
        """Submit one order and return its first known state."""
        self.require_connected()  # type: ignore[attr-defined]

        # Pre-flight capability checks
        if product not in ANGELONE_PRODUCT_MAP:
            raise CapabilityError(f"product {product.value} is not supported by Angel One")
        if intent.order_type not in ANGELONE_ORDER_TYPE_MAP:
            raise CapabilityError(
                f"order type {intent.order_type.value} is not supported by Angel One"
            )
        if intent.time_in_force not in ANGELONE_TIF_MAP:
            raise CapabilityError(
                f"time in force {intent.time_in_force.value} is not supported by Angel One"
            )

        # Idempotency check: if already placed with client_order_id, return cached report
        if client_order_id and client_order_id in self._orders_by_client_id:
            return self._orders_by_client_id[client_order_id]

        try:
            whole_quantity(intent.quantity)
        except ValueError as e:
            raise AdapterError(str(e)) from e

        for label, value in (("price", intent.price), ("trigger price", intent.trigger_price)):
            if value is not None and not (math.isfinite(value) and value > 0):
                raise AdapterError(f"{label} must be positive, got {value}")

        token = self._instruments.resolve_token(intent.instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {intent.instrument_id}")

        tradingsymbol, exchange = self._instruments.resolve_tradingsymbol_and_exchange(
            intent.instrument_id
        )

        payload = build_order_payload(
            intent=intent,
            product=product,
            client_order_id=client_order_id,
            tradingsymbol=tradingsymbol,
            symboltoken=token,
            exchange=exchange,
        )

        if client_order_id and client_order_id in self._ambiguous:
            with contextlib.suppress(Exception):
                book = await self.orders()
                for o in book:
                    if o.venue_order_id == client_order_id:
                        self._ambiguous.discard(client_order_id)
                        return o

        try:
            resp = await self._http.post(
                "/rest/secure/angelbroking/order/v1/placeOrder",
                pool="orders",
                json_body=payload,
            )
        except AngelOneRejectedError as rejection:
            report = OrderReport(
                order_id=f"REJ_{client_order_id or 'order'}",
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=float(intent.quantity),
                status=OrderStatus.REJECTED,
                product=product,
                order_type=intent.order_type,
                time_in_force=intent.time_in_force,
                price=intent.price,
                trigger_price=intent.trigger_price,
                reject_reason=rejection.message,
            )
            if client_order_id:
                self._orders_by_client_id[client_order_id] = report
            return report
        except AngelOneTransportError:
            if client_order_id:
                self._ambiguous.add(client_order_id)
            raise

        order_id = str(resp.get("orderid") if isinstance(resp, dict) else resp)

        # Retrieve order report from order book to reflect venue state
        with contextlib.suppress(Exception):
            book = await self.orders()
            for o in book:
                if o.order_id == order_id:
                    if client_order_id:
                        self._orders_by_client_id[client_order_id] = o
                    self._orders_by_id[order_id] = o
                    return o

        # Fallback optimistic report
        report = OrderReport(
            order_id=order_id,
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=float(intent.quantity),
            status=OrderStatus.ACCEPTED,
            product=product,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            price=intent.price,
            trigger_price=intent.trigger_price,
        )
        self._orders_by_id[order_id] = report
        if client_order_id:
            self._orders_by_client_id[client_order_id] = report
        return report

    async def modify_order(
        self,
        order_id: str,
        *,
        price: float | None = None,
        trigger_price: float | None = None,
        quantity: float | None = None,
    ) -> OrderReport:
        """Modify price or quantity of an active order."""
        self.require_connected()  # type: ignore[attr-defined]

        existing = self._orders_by_id.get(order_id)
        if existing is None:
            book = await self.orders()
            existing = next((o for o in book if o.order_id == order_id), None)
            if existing is None:
                raise AdapterError(f"order {order_id} not found")

        tradingsymbol, exchange = self._instruments.resolve_tradingsymbol_and_exchange(
            existing.instrument_id
        )
        token = self._instruments.resolve_token(existing.instrument_id) or "0"

        payload = build_modify_payload(
            order_id=order_id,
            tradingsymbol=tradingsymbol,
            symboltoken=token,
            exchange=exchange,
            order_type=existing.order_type,
            product=existing.product,
            new_price=price,
            new_quantity=quantity,
            current_price=existing.price,
            current_quantity=existing.quantity,
        )

        await self._http.post(
            "/rest/secure/angelbroking/order/v1/modifyOrder",
            pool="orders",
            json_body=payload,
        )

        # Refresh order state
        with contextlib.suppress(Exception):
            book = await self.orders()
            for o in book:
                if o.order_id == order_id:
                    self._orders_by_id[order_id] = o
                    return o

        updated = OrderReport(
            order_id=order_id,
            instrument_id=existing.instrument_id,
            side=existing.side,
            quantity=float(quantity) if quantity is not None else existing.quantity,
            status=OrderStatus.ACCEPTED,
            product=existing.product,
            order_type=existing.order_type,
            time_in_force=existing.time_in_force,
            price=price if price is not None else existing.price,
            trigger_price=trigger_price if trigger_price is not None else existing.trigger_price,
            filled_quantity=existing.filled_quantity,
            average_price=existing.average_price,
        )
        self._orders_by_id[order_id] = updated
        return updated

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an open order by broker order_id."""
        self.require_connected()  # type: ignore[attr-defined]

        existing = self._orders_by_id.get(order_id)
        if existing is None:
            book = await self.orders()
            existing = next((o for o in book if o.order_id == order_id), None)
            if existing is None:
                raise AdapterError(f"order {order_id} not found")

        if existing.status == OrderStatus.CANCELLED:
            # Idempotent cancel
            return

        payload = {
            "variety": "NORMAL",
            "orderid": str(order_id),
        }

        try:
            await self._http.post(
                "/rest/secure/angelbroking/order/v1/cancelOrder",
                pool="orders",
                json_body=payload,
            )
        except AngelOneRejectedError as e:
            if "not found" in e.message.lower() or "already cancelled" in e.message.lower():
                return
            raise

        # Update local cached state
        if order_id in self._orders_by_id:
            old = self._orders_by_id[order_id]
            self._orders_by_id[order_id] = OrderReport(
                order_id=old.order_id,
                instrument_id=old.instrument_id,
                side=old.side,
                quantity=old.quantity,
                status=OrderStatus.CANCELLED,
                product=old.product,
                order_type=old.order_type,
                time_in_force=old.time_in_force,
                price=old.price,
                trigger_price=old.trigger_price,
                filled_quantity=old.filled_quantity,
                average_price=old.average_price,
            )

    async def cancel_all(self) -> None:
        """Cancel all active open orders."""
        self.require_connected()  # type: ignore[attr-defined]
        book = await self.orders()
        for order in book:
            if order.status in _LIVE_STATUSES:
                with contextlib.suppress(Exception):
                    await self.cancel_order(order.order_id)

    async def order_status(self, order_id: str) -> OrderReport:
        """Retrieve latest known status for an order."""
        self.require_connected()  # type: ignore[attr-defined]
        book = await self.orders()
        for o in book:
            if o.order_id == order_id:
                self._orders_by_id[order_id] = o
                return o
        if order_id in self._orders_by_id:
            return self._orders_by_id[order_id]
        raise AdapterError(f"order {order_id} not found")

    async def orders(self) -> list[OrderReport]:
        """Fetch all orders from the broker's order book."""
        self.require_connected()  # type: ignore[attr-defined]
        data = await self._http.get(
            "/rest/secure/angelbroking/order/v1/getOrderBook", pool="orders"
        )
        items = (
            data
            if isinstance(data, list)
            else (data.get("data", []) if isinstance(data, dict) else [])
        )

        result: list[OrderReport] = []
        for item in items:
            if isinstance(item, dict):
                report = parse_order(item, resolver=self._id_resolver)
                result.append(report)
                self._orders_by_id[report.order_id] = report
        return result

    async def trades(self) -> list[Trade]:
        """Fetch executed trade fills from the broker's trade book."""
        self.require_connected()  # type: ignore[attr-defined]
        data = await self._http.get(
            "/rest/secure/angelbroking/order/v1/getTradeBook", pool="orders"
        )
        items = (
            data
            if isinstance(data, list)
            else (data.get("data", []) if isinstance(data, dict) else [])
        )

        result: list[Trade] = []
        for item in items:
            if isinstance(item, dict):
                result.append(parse_trade(item, resolver=self._id_resolver))
        return result

    async def positions(self) -> list[Position]:
        """Fetch open and closed positions."""
        self.require_connected()  # type: ignore[attr-defined]
        data = await self._http.get("/rest/secure/angelbroking/order/v1/getPosition", pool="orders")
        items = (
            data
            if isinstance(data, list)
            else (data.get("data", []) if isinstance(data, dict) else [])
        )

        result: list[Position] = []
        for item in items:
            if isinstance(item, dict):
                result.append(parse_position(item, resolver=self._id_resolver))
        return result

    async def holdings(self) -> list[Holding]:
        """Fetch demat equity holdings."""
        self.require_connected()  # type: ignore[attr-defined]
        data = await self._http.get(
            "/rest/secure/angelbroking/portfolio/v1/getHolding", pool="data"
        )
        items = (
            data
            if isinstance(data, list)
            else (data.get("data", []) if isinstance(data, dict) else [])
        )

        result: list[Holding] = []
        for item in items:
            if isinstance(item, dict):
                result.append(parse_holding(item, resolver=self._id_resolver))
        return result

    async def funds(self) -> Funds:
        """Fetch cash ledger balances and RMS limits."""
        self.require_connected()  # type: ignore[attr-defined]
        data = await self._http.get("/rest/secure/angelbroking/user/v1/getRMS", pool="data")
        return parse_funds(data if isinstance(data, dict) else {})

    async def margin(
        self,
        order: OrderIntent | None = None,
        product: Product | None = None,
    ) -> MarginReport:
        """Fetch margin requirement for an order or account."""
        self.require_connected()  # type: ignore[attr-defined]
        instrument_id = order.instrument_id if order else None
        data = await self._http.get("/rest/secure/angelbroking/user/v1/getRMS", pool="data")
        return parse_margin_report(
            data if isinstance(data, dict) else {}, instrument_id=instrument_id
        )


__all__ = ["AngelOneExecutionMixin"]
