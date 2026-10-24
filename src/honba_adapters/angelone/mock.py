"""In-memory mock HTTP transport for Angel One SmartAPI 2.0 (ADR 0010 sandbox / paper testing)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

__all__ = ["make_angelone_mock_handler", "make_angelone_mock_transport"]

_DEFAULT_SCRIP_MASTER = [
    {
        "token": "2885",
        "symbol": "RELIANCE-EQ",
        "name": "RELIANCE INDUSTRIES",
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": "",
        "exch_seg": "NSE",
        "tick_size": "5.000000",
    },
    {
        "token": "1594",
        "symbol": "INFY-EQ",
        "name": "INFOSYS LIMITED",
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": "",
        "exch_seg": "NSE",
        "tick_size": "5.000000",
    },
    {
        "token": "3045",
        "symbol": "SBIN-EQ",
        "name": "STATE BANK OF INDIA",
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": "",
        "exch_seg": "NSE",
        "tick_size": "5.000000",
    },
    {
        "token": "4300",
        "symbol": "GOLD",
        "name": "GOLD",
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": "",
        "exch_seg": "MCX",
        "tick_size": "100.000000",
    },
    {
        "token": "99926000",
        "symbol": "NIFTY 50",
        "name": "NIFTY 50",
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": "",
        "exch_seg": "NSE",
        "tick_size": "5.000000",
    },
]


def make_angelone_mock_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Return an in-memory SmartAPI 2.0 REST handler simulating stateful execution."""
    orders_store: dict[str, dict[str, Any]] = {}
    trades_store: list[dict[str, Any]] = []
    positions_store: list[dict[str, Any]] = []
    order_seq = [1000]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        body: dict[str, Any] = {}
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = {}

        # Morning login
        if "loginByPassword" in path and method == "POST":
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": {
                        "jwtToken": "mock_jwt_token_valid",
                        "refreshToken": "mock_refresh_token_valid",
                        "feedToken": "mock_feed_token_valid",
                    },
                },
            )

        # Token refresh
        if "generateTokens" in path and method == "POST":
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": {
                        "jwtToken": "new_refreshed_jwt_token",
                        "refreshToken": "new_refreshed_refresh_token",
                        "feedToken": "new_refreshed_feed_token",
                    },
                },
            )

        # Profile / session validation
        if "getProfile" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": {
                        "clientcode": "mock_client_code",
                        "name": "HONBA TEST CLIENT",
                        "email": "test@honba.io",
                    },
                },
            )

        # RMS funds / limits
        if "getRMS" in path or "getRms" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": {
                        "equityamount": "1000000.00",
                        "equitylimit": "1000000.00",
                        "commodityamount": "0.00",
                        "availablecash": "1000000.00",
                        "collateral": "0.00",
                    },
                },
            )

        # Margin requirements
        if "margin" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": {
                        "totalMargin": "5000.00",
                        "maintenanceMargin": "4000.00",
                    },
                },
            )

        # Scrip master JSON download
        if "OpenAPIScripMaster" in path:
            return httpx.Response(200, json=_DEFAULT_SCRIP_MASTER)

        # Market quote
        if "quote" in path:
            symbol = body.get("tradingsymbol", "RELIANCE-EQ")
            token = body.get("symboltoken", "2885")
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": {
                        "tradingsymbol": symbol,
                        "symboltoken": token,
                        "exchange": body.get("exchange", "NSE"),
                        "ltp": "2450.00",
                        "open": "2400.00",
                        "high": "2460.00",
                        "low": "2390.00",
                        "close": "2410.00",
                        "exchange_timestamp": "2023-11-15T09:15:00+05:30",
                        "depth": {
                            "buy": [
                                {"price": "2449.00", "quantity": "100", "orders": 1},
                                {"price": "2448.50", "quantity": "200", "orders": 2},
                            ],
                            "sell": [
                                {"price": "2451.00", "quantity": "150", "orders": 1},
                                {"price": "2452.00", "quantity": "300", "orders": 3},
                            ],
                        },
                    },
                },
            )

        # Historical candles
        if "getCandleData" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": [
                        ["2023-11-15T09:15:00+05:30", 2400.0, 2420.0, 2390.0, 2410.0, 1000],
                        ["2023-11-15T09:16:00+05:30", 2410.0, 2430.0, 2400.0, 2425.0, 1500],
                        ["2023-11-15T09:17:00+05:30", 2425.0, 2450.0, 2420.0, 2445.0, 2000],
                    ],
                },
            )

        # Order placement
        if "placeOrder" in path and method == "POST":
            order_seq[0] += 1
            order_id = f"20113000000{order_seq[0]}"

            symbol = body.get("tradingsymbol", "RELIANCE")
            exchange = body.get("exchange", "NSE")
            ordertype = body.get("ordertype", "MARKET")
            price = body.get("price", "0")
            qty = body.get("quantity", "1")
            side = body.get("transactiontype", "BUY")

            status = "OPEN"
            filled_shares = "0"
            avg_price = "0.00"

            order_record = {
                "variety": body.get("variety", "NORMAL"),
                "orderid": order_id,
                "exchange": exchange,
                "symboltoken": body.get("symboltoken", "2885"),
                "producttype": body.get("producttype", "DELIVERY"),
                "tradingsymbol": symbol,
                "duration": body.get("duration", "DAY"),
                "price": price,
                "triggerprice": body.get("triggerprice", "0"),
                "quantity": qty,
                "transactiontype": side,
                "ordertype": ordertype,
                "status": status,
                "averageprice": avg_price,
                "filledshares": filled_shares,
                "lastmodifiedtime": "2023-11-15 09:15:00",
                "exchangetimestamp": "2023-11-15 09:15:00",
                "text": "",
            }
            orders_store[order_id] = order_record

            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": {
                        "orderid": order_id,
                        "uniqueorderid": f"uniq_{order_id}",
                    },
                },
            )

        # Order modification
        if "modifyOrder" in path and method in ("POST", "PUT"):
            order_id = str(body.get("orderid", ""))
            if order_id in orders_store:
                rec = orders_store[order_id]
                if "price" in body:
                    rec["price"] = body["price"]
                if "quantity" in body:
                    rec["quantity"] = body["quantity"]
                rec["status"] = "MODIFIED"
                return httpx.Response(
                    200,
                    json={
                        "status": True,
                        "message": "SUCCESS",
                        "errorcode": "",
                        "data": {"orderid": order_id},
                    },
                )
            return httpx.Response(
                400,
                json={
                    "status": False,
                    "message": f"Order {order_id} not found",
                    "errorcode": "AB2001",
                },
            )

        # Order cancellation
        if "cancelOrder" in path and method in ("POST", "PUT"):
            order_id = str(body.get("orderid", ""))
            if order_id in orders_store:
                orders_store[order_id]["status"] = "CANCELLED"
                return httpx.Response(
                    200,
                    json={
                        "status": True,
                        "message": "SUCCESS",
                        "errorcode": "",
                        "data": {"orderid": order_id},
                    },
                )
            return httpx.Response(
                400,
                json={
                    "status": False,
                    "message": f"Order {order_id} not found",
                    "errorcode": "AB2001",
                },
            )

        # Order book
        if "getOrderBook" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": list(orders_store.values()),
                },
            )

        # Trade book
        if "getTradeBook" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": trades_store,
                },
            )

        # Positions
        if "getPosition" in path or "getPositions" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": positions_store,
                },
            )

        # Holdings
        if "getHolding" in path:
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "SUCCESS",
                    "errorcode": "",
                    "data": [
                        {
                            "tradingsymbol": "SBIN-EQ",
                            "exchange": "NSE",
                            "quantity": "50",
                            "averageprice": "500.00",
                            "ltp": "550.00",
                        }
                    ],
                },
            )

        # Fallback 200
        return httpx.Response(200, json={"status": True, "message": "OK", "data": {}})

    return handler


def make_angelone_mock_transport() -> httpx.MockTransport:
    """Create a mock transport running the in-memory SmartAPI handler."""
    return httpx.MockTransport(make_angelone_mock_handler())
