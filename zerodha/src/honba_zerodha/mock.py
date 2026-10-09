"""In-memory mock HTTP transport for Zerodha Kite Connect v3 (ADR 0010 sandbox / paper testing)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx

__all__ = ["make_zerodha_mock_transport"]


def make_zerodha_mock_transport() -> httpx.MockTransport:
    """Creates an in-memory HTTP mock transport responding as Kite Connect v3 REST API."""
    orders_store: dict[str, dict[str, Any]] = {}
    order_seq = [1000]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        # Parse request body (may be form-encoded or JSON)
        content_type = request.headers.get("content-type", "")
        body: dict[str, Any] = {}
        if request.content:
            if "application/json" in content_type:
                body = json.loads(request.content.decode("utf-8"))
            else:
                parsed_qs = parse_qs(request.content.decode("utf-8"))
                body = {k: v[0] for k, v in parsed_qs.items()}

        if path.endswith("/user/margins"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "equity": {
                            "net": 1_000_000.0,
                            "available": {
                                "cash": 1_000_000.0,
                                "opening_balance": 1_000_000.0,
                                "collateral": 0.0,
                            },
                            "utilised": {
                                "debits": 50_000.0,
                                "margin_used": 50_000.0,
                            },
                        }
                    },
                },
            )

        if path.endswith("/quote"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "NSE:RELIANCE": {
                            "instrument_token": 738561,
                            "last_price": 2450.0,
                            "depth": {
                                "buy": [{"price": 2449.0, "quantity": 100}],
                                "sell": [{"price": 2451.0, "quantity": 100}],
                            },
                        }
                    },
                },
            )

        if "/instruments/historical" in path:
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "candles": [
                            [
                                "2023-11-15T09:15:00+05:30",
                                2400.0,
                                2420.0,
                                2390.0,
                                2410.0,
                                1000.0,
                                0,
                            ],
                            [
                                "2023-11-15T09:16:00+05:30",
                                2410.0,
                                2430.0,
                                2400.0,
                                2425.0,
                                1500.0,
                                0,
                            ],
                        ]
                    },
                },
            )

        if path.endswith("/orders/regular") and method == "POST":
            qty = float(body.get("quantity", 1))
            if qty > 999_999:
                return httpx.Response(
                    400,
                    json={
                        "status": "error",
                        "error_type": "OrderException",
                        "message": "Insufficient margin available in account",
                    },
                )
            order_seq[0] += 1
            order_id = f"KITE-ORD-{order_seq[0]}"
            orders_store[order_id] = {
                "order_id": order_id,
                "tradingsymbol": str(body.get("tradingsymbol", "RELIANCE")),
                "exchange": str(body.get("exchange", "NSE")),
                "transaction_type": str(body.get("transaction_type", "BUY")),
                "order_type": str(body.get("order_type", "MARKET")),
                "product": str(body.get("product", "CNC")),
                "status": "OPEN",
                "quantity": qty,
                "price": float(body["price"]) if "price" in body else None,
                "filled_quantity": 0.0,
                "tag": body.get("tag"),
            }
            return httpx.Response(
                200,
                json={"status": "success", "data": {"order_id": order_id}},
            )

        if path.endswith("/orders") and method == "GET":
            return httpx.Response(
                200,
                json={"status": "success", "data": list(orders_store.values())},
            )

        if "/orders/regular/" in path or "/orders/" in path:
            order_id = path.rstrip("/").split("/")[-1]
            if method == "GET":
                if order_id in orders_store:
                    return httpx.Response(
                        200,
                        json={"status": "success", "data": [orders_store[order_id]]},
                    )
                return httpx.Response(
                    404,
                    json={
                        "status": "error",
                        "error_type": "OrderException",
                        "message": "Order not found",
                    },
                )
            if method == "PUT":
                if order_id in orders_store:
                    if "quantity" in body:
                        orders_store[order_id]["quantity"] = float(body["quantity"])
                    if "price" in body:
                        orders_store[order_id]["price"] = float(body["price"])
                    return httpx.Response(
                        200,
                        json={"status": "success", "data": {"order_id": order_id}},
                    )
                return httpx.Response(
                    404,
                    json={
                        "status": "error",
                        "error_type": "OrderException",
                        "message": "Order not found",
                    },
                )
            if method == "DELETE":
                if order_id in orders_store:
                    orders_store[order_id]["status"] = "CANCELLED"
                    return httpx.Response(
                        200,
                        json={"status": "success", "data": {"order_id": order_id}},
                    )
                return httpx.Response(
                    404,
                    json={
                        "status": "error",
                        "error_type": "OrderException",
                        "message": "Order not found",
                    },
                )

        if path.endswith("/trades"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [
                        {
                            "trade_id": "T-1001",
                            "order_id": "KITE-ORD-1001",
                            "tradingsymbol": "RELIANCE",
                            "exchange": "NSE",
                            "transaction_type": "BUY",
                            "quantity": 1.0,
                            "average_price": 2450.0,
                            "brokerage": 20.0,
                        }
                    ],
                },
            )

        if path.endswith("/portfolio/positions"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "net": [
                            {
                                "tradingsymbol": "RELIANCE",
                                "exchange": "NSE",
                                "quantity": 1.0,
                                "buy_price": 2450.0,
                                "realised": 0.0,
                                "unrealised": 0.0,
                            }
                        ]
                    },
                },
            )

        if path.endswith("/portfolio/holdings"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [
                        {
                            "tradingsymbol": "RELIANCE",
                            "exchange": "NSE",
                            "quantity": 10.0,
                            "average_price": 2450.0,
                            "last_price": 2455.0,
                            "pnl": 50.0,
                        }
                    ],
                },
            )

        if path.endswith("/margins/orders"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [{"total": 50_000.0, "span": 40_000.0, "available": 100_000.0}],
                },
            )

        return httpx.Response(
            404,
            json={"status": "error", "message": f"Not found: {path}"},
        )

    return httpx.MockTransport(handler)
