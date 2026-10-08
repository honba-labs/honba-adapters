"""ExecutionAdapter implementation for Zerodha Kite Connect v3."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from honba.adapters.errors import AdapterError, CapabilityError
from honba.adapters.models import Funds, Holding, MarginReport, OrderReport, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderStatus
from honba.domain.position import Position
from honba.domain.trade import Trade

from honba_zerodha.constants import (
    ZERODHA_ORDER_TYPE_MAP,
    ZERODHA_PRODUCT_MAP,
    ZERODHA_STATUS_MAP,
)
from honba_zerodha.parsing import (
    build_order_payload,
    parse_funds,
    parse_holding,
    parse_margin_report,
    parse_order,
    parse_position,
    parse_trade,
)

if TYPE_CHECKING:
    from honba_zerodha.http import ZerodhaHttpClient
    from honba_zerodha.instruments import ZerodhaInstrumentMaster

logger = logging.getLogger("honba.adapters.zerodha.execution")

__all__ = ["ZerodhaExecutionMixin"]


class ZerodhaExecutionMixin:
    """Implements ExecutionAdapter methods for ZerodhaAdapter."""

    _http: ZerodhaHttpClient
    _instruments: ZerodhaInstrumentMaster
    _orders_by_id: dict[str, OrderReport]
    _orders_by_client_id: dict[str, OrderReport]

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
        if product not in ZERODHA_PRODUCT_MAP:
            raise CapabilityError(f"product {product.value} is not supported by Zerodha")
        if intent.order_type not in ZERODHA_ORDER_TYPE_MAP:
            raise CapabilityError(
                f"order type {intent.order_type.value} is not supported by Zerodha"
            )

        # Idempotency check: if already placed with client_order_id, return cached report
        if client_order_id and client_order_id in self._orders_by_client_id:
            return self._orders_by_client_id[client_order_id]

        # Verify instrument resolution
        token = self._instruments.resolve_token(intent.instrument_id)
        if token is None:
            raise AdapterError(f"unknown instrument {intent.instrument_id}")

        symbol, exchange = self._instruments.resolve_tradingsymbol_and_exchange(
            intent.instrument_id
        )

        payload = build_order_payload(
            intent=intent,
            product=product,
            client_order_id=client_order_id,
            tradingsymbol=symbol,
            exchange=exchange,
        )

        try:
            resp = await self._http.post(
                "/orders/regular",
                pool="orders",
                data=payload,
            )
        except AdapterError as e:
            # If server returned an operational error that represents an order reject
            err_text = str(e)
            if (
                "REJECT" in err_text.upper()
                or "RMS" in err_text.upper()
                or "INSUFFICIENT" in err_text.upper()
                or "MARGIN" in err_text.upper()
            ):
                report = OrderReport(
                    order_id=f"REJ-{client_order_id or '0'}",
                    instrument_id=intent.instrument_id,
                    side=intent.side,
                    quantity=intent.quantity,
                    status=OrderStatus.REJECTED,
                    product=product,
                    order_type=intent.order_type,
                    reject_reason=err_text,
                )
                if client_order_id:
                    self._orders_by_client_id[client_order_id] = report
                return report
            raise

        order_id = str(resp.get("order_id") if isinstance(resp, dict) else resp or "")
        order_status_str = str(
            resp.get("order_status") or resp.get("status") or "OPEN"
            if isinstance(resp, dict)
            else "OPEN"
        ).upper()

        if order_status_str in ("REJECTED", "FAILURE"):
            reason = (
                resp.get("status_message")
                or resp.get("rejection_reason")
                or resp.get("message")
                or "Rejected by broker RMS"
                if isinstance(resp, dict)
                else "Rejected by broker RMS"
            )
            report = OrderReport(
                order_id=order_id or f"REJ-{client_order_id or '0'}",
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=intent.quantity,
                status=OrderStatus.REJECTED,
                product=product,
                order_type=intent.order_type,
                reject_reason=reason,
            )
        else:
            status = ZERODHA_STATUS_MAP.get(order_status_str, OrderStatus.ACCEPTED)
            report = OrderReport(
                order_id=order_id,
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=intent.quantity,
                status=status,
                product=product,
                order_type=intent.order_type,
                price=intent.price,
                trigger_price=intent.trigger_price,
            )

        if order_id:
            self._orders_by_id[order_id] = report
        if client_order_id:
            self._orders_by_client_id[client_order_id] = report

        return report

    async def modify_order(
        self,
        order_id: str,
        *,
        quantity: float | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
    ) -> None:
        """Modify quantity, price, or trigger price of an existing order."""
        self.require_connected()  # type: ignore[attr-defined]

        if quantity is None and price is None and trigger_price is None:
            raise AdapterError(
                "at least one of quantity, price, or trigger_price must be specified"
            )

        payload: dict[str, Any] = {}
        if quantity is not None:
            payload["quantity"] = int(quantity)
        if price is not None:
            payload["price"] = float(price)
        if trigger_price is not None:
            payload["trigger_price"] = float(trigger_price)

        try:
            await self._http.put(
                f"/orders/regular/{order_id}",
                pool="orders",
                data=payload,
            )
        except AdapterError as e:
            raise AdapterError(f"unknown order id {order_id}") from e

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an open order."""
        self.require_connected()  # type: ignore[attr-defined]
        cached = self._orders_by_id.get(order_id)
        if cached is not None and cached.status in (
            OrderStatus.CANCELLED,
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
        ):
            return  # Terminal orders are reported, not re-cancelled (no-op)

        try:
            await self._http.delete(f"/orders/regular/{order_id}", pool="orders")
            if cached is not None:
                self._orders_by_id[order_id] = OrderReport(
                    order_id=cached.order_id,
                    instrument_id=cached.instrument_id,
                    side=cached.side,
                    quantity=cached.quantity,
                    status=OrderStatus.CANCELLED,
                    product=cached.product,
                    order_type=cached.order_type,
                    price=cached.price,
                    trigger_price=cached.trigger_price,
                    filled_quantity=cached.filled_quantity,
                    average_price=cached.average_price,
                )
        except AdapterError as e:
            raise AdapterError(f"unknown order id {order_id}") from e

    async def cancel_all(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        product: Product | None = None,
    ) -> None:
        """Cancel all open orders, optionally filtered by instrument or product."""
        self.require_connected()  # type: ignore[attr-defined]
        all_orders = await self.orders()
        live_statuses = {
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        }

        for ord_rep in all_orders:
            if ord_rep.status in live_statuses:
                if instrument_id is not None and ord_rep.instrument_id != instrument_id:
                    continue
                if product is not None and ord_rep.product != product:
                    continue
                with contextlib.suppress(Exception):
                    await self.cancel_order(ord_rep.order_id)

    async def order_status(self, order_id: str) -> OrderReport:
        """Fetch current state of one order."""
        self.require_connected()  # type: ignore[attr-defined]
        try:
            resp = await self._http.get(f"/orders/{order_id}", pool="orders")
        except AdapterError as e:
            raise AdapterError(f"unknown order id {order_id}") from e

        if not resp:
            raise AdapterError(f"unknown order id {order_id}")

        history = resp if isinstance(resp, list) else resp.get("data", [resp])
        if not history:
            raise AdapterError(f"unknown order id {order_id}")

        # The last element in the history array contains the latest order state
        latest_item = history[-1]
        report = parse_order(latest_item, self._id_resolver)
        self._orders_by_id[order_id] = report
        return report

    async def orders(self) -> list[OrderReport]:
        """Fetch all orders for the current trading day."""
        self.require_connected()  # type: ignore[attr-defined]
        resp = await self._http.get("/orders", pool="orders")
        if not isinstance(resp, list):
            resp = resp.get("data", []) if isinstance(resp, dict) else []

        reports: list[OrderReport] = []
        for item in resp:
            try:
                rep = parse_order(item, self._id_resolver)
                reports.append(rep)
                self._orders_by_id[rep.order_id] = rep
            except (KeyError, ValueError, TypeError) as e:
                logger.debug("Skipping unparseable order item: %s", e)
                continue

        # Merge with in-memory orders placed during this session if not yet in broker dump
        for mem_rep in self._orders_by_id.values():
            if not any(r.order_id == mem_rep.order_id for r in reports):
                reports.append(mem_rep)

        return reports

    async def trades(self) -> list[Trade]:
        """Fetch all executed fills for the current trading day."""
        self.require_connected()  # type: ignore[attr-defined]
        resp = await self._http.get("/trades", pool="orders")
        if not isinstance(resp, list):
            resp = resp.get("data", []) if isinstance(resp, dict) else []

        result: list[Trade] = []
        for item in resp:
            try:
                t = parse_trade(item, self._id_resolver)
                result.append(t)
            except (KeyError, ValueError, TypeError) as e:
                logger.debug("Skipping unparseable trade item: %s", e)
                continue
        return result

    async def positions(self) -> list[Position]:
        """Fetch all current trading positions."""
        self.require_connected()  # type: ignore[attr-defined]
        resp = await self._http.get("/portfolio/positions", pool="orders")
        positions_data = (
            resp.get("net", [])
            if isinstance(resp, dict) and "net" in resp
            else (resp if isinstance(resp, list) else [])
        )

        result: list[Position] = []
        for item in positions_data:
            try:
                pos = parse_position(item, self._id_resolver)
                result.append(pos)
            except (KeyError, ValueError, TypeError) as e:
                logger.debug("Skipping unparseable position item: %s", e)
                continue
        return result

    async def holdings(self) -> list[Holding]:
        """Fetch demat holdings."""
        self.require_connected()  # type: ignore[attr-defined]
        resp = await self._http.get("/portfolio/holdings", pool="orders")
        if not isinstance(resp, list):
            resp = resp.get("data", []) if isinstance(resp, dict) else []

        result: list[Holding] = []
        for item in resp:
            try:
                h = parse_holding(item, self._id_resolver)
                result.append(h)
            except (KeyError, ValueError, TypeError) as e:
                logger.debug("Skipping unparseable holding item: %s", e)
                continue
        return result

    async def funds(self) -> Funds:
        """Fetch available cash and margin snapshot."""
        self.require_connected()  # type: ignore[attr-defined]
        resp = await self._http.get("/user/margins", pool="orders")
        return parse_funds(resp if isinstance(resp, dict) else {})

    async def margin(self, instrument_id: InstrumentId | None = None) -> MarginReport:
        """Fetch margin requirement."""
        self.require_connected()  # type: ignore[attr-defined]
        if instrument_id is not None:
            token = self._instruments.resolve_token(instrument_id)
            if token is None:
                raise AdapterError(f"unknown instrument {instrument_id}")

            payload = [
                {
                    "exchange": instrument_id.exchange,
                    "tradingsymbol": instrument_id.symbol,
                    "transaction_type": "BUY",
                    "variety": "regular",
                    "product": "CNC",
                    "order_type": "MARKET",
                    "quantity": 1,
                }
            ]
            with contextlib.suppress(Exception):
                resp = await self._http.post("/margins/orders", pool="orders", json_body=payload)
                item = resp[0] if isinstance(resp, list) and resp else resp
                return parse_margin_report(item if isinstance(item, dict) else {}, instrument_id)

        funds = await self.funds()
        return MarginReport(
            initial=funds.available_cash + funds.margin_used,
            maintenance=funds.margin_used,
            currency="INR",
            instrument_id=instrument_id,
        )
