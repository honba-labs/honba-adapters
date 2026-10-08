"""Contract certification suite for DhanAdapter (M2).

Runs the full Honba adapter contract suite against DhanAdapter with mock HTTP transport.
Verifies descriptor, lifecycle, capability enforcement, market data, and execution round-tripping.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from honba.adapters.contract import verify_adapter_contract

from honba_dhan import DhanAdapter, DhanConfig


def make_dhan_mock_transport() -> httpx.MockTransport:
    """Creates an in-memory HTTP mock transport responding as Dhan v2 REST API."""
    orders_store: dict[str, dict[str, Any]] = {}
    order_seq = [1000]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body = json.loads(request.content.decode("utf-8")) if request.content else {}

        if path.endswith("/fundlimit"):
            return httpx.Response(
                200,
                json={
                    "availabelBalance": 1_000_000.0,
                    "sodLimit": 1_000_000.0,
                    "utilizedAmount": 50_000.0,
                    "collateralAmount": 0.0,
                    "receiveableAmount": 0.0,
                    "blockedPayoutAmount": 0.0,
                },
            )

        if path.endswith("/marketfeed/quote"):
            return httpx.Response(
                200,
                json={
                    "lastPrice": 2450.0,
                    "depth": {
                        "buy": [{"price": 2449.0, "quantity": 100}],
                        "sell": [{"price": 2451.0, "quantity": 100}],
                    },
                },
            )

        if path.endswith(("/charts/historical", "/charts/intraday")):
            return httpx.Response(
                200,
                json={
                    "open": [2400.0, 2410.0],
                    "high": [2420.0, 2430.0],
                    "low": [2390.0, 2400.0],
                    "close": [2410.0, 2425.0],
                    "volume": [1000.0, 1500.0],
                    "start_Time": [1700000000.0, 1700086400.0],
                },
            )

        if path.endswith("/orders") and method == "POST":
            qty = float(body.get("quantity", 1))
            if qty > 999_999:
                return httpx.Response(
                    200,
                    json={
                        "orderId": "DHAN-REJ-01",
                        "orderStatus": "REJECTED",
                        "remarks": "Insufficient margin available in account",
                    },
                )
            order_seq[0] += 1
            order_id = f"DHAN-ORD-{order_seq[0]}"
            orders_store[order_id] = {
                "orderId": order_id,
                "securityId": str(body.get("securityId", "1333")),
                "exchangeSegment": str(body.get("exchangeSegment", "NSE_EQ")),
                "transactionType": str(body.get("transactionType", "BUY")),
                "orderType": str(body.get("orderType", "MARKET")),
                "productType": str(body.get("productType", "CNC")),
                "orderStatus": "PENDING",
                "quantity": qty,
                "price": body.get("price"),
                "filledQty": 0.0,
            }
            return httpx.Response(
                200,
                json={"orderId": order_id, "orderStatus": "PENDING"},
            )

        if path.endswith("/orders") and method == "GET":
            return httpx.Response(200, json=list(orders_store.values()))

        if "/orders/" in path:
            order_id = path.split("/orders/")[-1]
            if method == "GET":
                if order_id in orders_store:
                    return httpx.Response(200, json=orders_store[order_id])
                return httpx.Response(404, json={"status": "error", "message": "Order not found"})
            if method == "PUT":
                if order_id in orders_store:
                    if "quantity" in body:
                        orders_store[order_id]["quantity"] = body["quantity"]
                    if "price" in body:
                        orders_store[order_id]["price"] = body["price"]
                    return httpx.Response(200, json={"status": "success"})
                return httpx.Response(404, json={"status": "error", "message": "Order not found"})
            if method == "DELETE":
                if order_id in orders_store:
                    orders_store[order_id]["orderStatus"] = "CANCELLED"
                    return httpx.Response(200, json={"status": "success"})
                return httpx.Response(404, json={"status": "error", "message": "Order not found"})

        if path.endswith("/trades"):
            return httpx.Response(
                200,
                json=[
                    {
                        "orderId": "DHAN-ORD-1001",
                        "securityId": "1333",
                        "exchangeSegment": "NSE_EQ",
                        "transactionType": "BUY",
                        "tradedQuantity": 1.0,
                        "tradedPrice": 2450.0,
                        "brokerageCharge": 15.0,
                    }
                ],
            )

        if path.endswith("/positions"):
            return httpx.Response(
                200,
                json=[
                    {
                        "securityId": "1333",
                        "exchangeSegment": "NSE_EQ",
                        "netQty": 1.0,
                        "costPrice": 2450.0,
                        "realizedProfit": 0.0,
                    }
                ],
            )

        if path.endswith("/holdings"):
            return httpx.Response(
                200,
                json=[
                    {
                        "securityId": "1333",
                        "exchangeSegment": "NSE_EQ",
                        "totalQty": 10.0,
                        "avgCostPrice": 2450.0,
                        "ltp": 2455.0,
                    }
                ],
            )

        if path.endswith("/margincalculator"):
            return httpx.Response(
                200,
                json={"totalMargin": 50_000.0, "maintenanceMargin": 40_000.0},
            )

        return httpx.Response(404, json={"status": "error", "message": f"Not found: {path}"})

    return httpx.MockTransport(handler)


class TestDhanContract:
    @pytest.mark.asyncio
    async def test_dhan_adapter_satisfies_contract(self) -> None:
        config = DhanConfig.paper()
        mock_transport = make_dhan_mock_transport()
        adapter = DhanAdapter(config, http_transport=mock_transport)

        # Runs the strict canonical contract verification suite from honba.adapters.contract
        await verify_adapter_contract(adapter)
