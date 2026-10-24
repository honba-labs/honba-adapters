"""Unit tests for WebSocket transport, reconnection, heartbeats, and sequence tracking (M1)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
import websockets

from honba_adapters.common.errors import SequenceGapError, WebSocketError
from honba_adapters.common.websocket import (
    ReconnectingWebSocketClient,
    SequenceTracker,
    WebSocketState,
)


class TestSequenceTracker:
    def test_initial_and_contiguous_sequence(self) -> None:
        tracker = SequenceTracker()
        assert tracker.next_expected is None
        assert tracker.last_received is None

        # First message at 100
        res = tracker.track(100)
        assert res.is_gap is False
        assert res.is_duplicate is False
        assert res.expected == 100
        assert res.received == 100
        assert tracker.next_expected == 101
        assert tracker.last_received == 100

        # Next contiguous message at 101
        res = tracker.track(101)
        assert res.is_gap is False
        assert res.is_duplicate is False
        assert res.expected == 101
        assert res.received == 101
        assert tracker.next_expected == 102

    def test_sequence_gap_detected(self) -> None:
        tracker = SequenceTracker(initial_sequence=1)
        assert tracker.next_expected == 1

        res = tracker.track(1)
        assert res.is_gap is False

        # Message 2 dropped, receiving 4 instead
        res = tracker.track(4)
        assert res.is_gap is True
        assert res.is_duplicate is False
        assert res.expected == 2
        assert res.received == 4
        assert res.gap_size == 2
        assert tracker.gaps_detected == 1
        assert tracker.next_expected == 5

    def test_duplicate_or_out_of_order_detected(self) -> None:
        tracker = SequenceTracker(initial_sequence=10)
        tracker.track(10)  # expects 11
        tracker.track(11)  # expects 12

        # Duplicate 10 received
        res = tracker.track(10)
        assert res.is_gap is False
        assert res.is_duplicate is True
        assert res.expected == 12
        assert res.received == 10
        assert tracker.duplicates_detected == 1
        assert tracker.next_expected == 12  # remains at 12

    def test_tracker_reset(self) -> None:
        tracker = SequenceTracker(initial_sequence=5)
        tracker.track(5)
        tracker.track(10)  # gap
        assert tracker.gaps_detected == 1

        tracker.reset(initial_sequence=1)
        assert tracker.next_expected == 1
        assert tracker.last_received is None
        assert tracker.gaps_detected == 0
        assert tracker.duplicates_detected == 0


@pytest.fixture
async def local_echo_server():
    """Starts a local WebSocket server that echoes back text messages."""
    clients: set[Any] = set()

    async def handler(ws):
        clients.add(ws)
        try:
            with contextlib.suppress(Exception):
                async for msg in ws:
                    await ws.send(msg)
        finally:
            clients.discard(ws)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}"

    yield {"url": url, "server": server, "clients": clients}

    server.close()
    await server.wait_closed()


class TestReconnectingWebSocketClient:
    @pytest.mark.asyncio
    async def test_connect_send_receive_stop(self, local_echo_server) -> None:
        url = local_echo_server["url"]
        connected = asyncio.Event()
        received_msgs: list[str] = []

        async def on_conn() -> None:
            connected.set()

        async def on_msg(data: str | bytes) -> None:
            received_msgs.append(data if isinstance(data, str) else data.decode("utf-8"))

        client = ReconnectingWebSocketClient(
            url,
            on_connected=on_conn,
            on_message=on_msg,
            ping_interval=0,  # disable heartbeat in test
        )

        async with client:
            await client.wait_connected(timeout=2.0)
            assert client.is_connected
            assert client.state == WebSocketState.CONNECTED
            assert connected.is_set()

            await client.send("hello honba")
            await asyncio.sleep(0.05)
            assert "hello honba" in received_msgs

            await client.send_json({"op": "ping", "id": 42})
            await asyncio.sleep(0.05)
            assert json.dumps({"op": "ping", "id": 42}) in received_msgs

        assert not client.is_connected
        assert client.state == WebSocketState.CLOSED

    @pytest.mark.asyncio
    async def test_send_while_disconnected_raises(self) -> None:
        client = ReconnectingWebSocketClient("ws://127.0.0.1:9999")
        with pytest.raises(WebSocketError, match="not connected"):
            await client.send("test")

    @pytest.mark.asyncio
    async def test_subscription_management(self) -> None:
        client = ReconnectingWebSocketClient("ws://127.0.0.1:9999")
        client.add_subscriptions("NSE:INFY", "NSE:TCS")
        assert client.subscriptions == frozenset({"NSE:INFY", "NSE:TCS"})

        client.add_subscriptions("NSE:RELIANCE")
        assert len(client.subscriptions) == 3

        client.remove_subscriptions("NSE:INFY")
        assert client.subscriptions == frozenset({"NSE:TCS", "NSE:RELIANCE"})

        client.clear_subscriptions()
        assert len(client.subscriptions) == 0

    @pytest.mark.asyncio
    async def test_sequence_tracking_in_stream(self, local_echo_server) -> None:
        url = local_echo_server["url"]
        gaps: list[int] = []

        def extract_seq(msg: str | bytes) -> int | None:
            try:
                payload = json.loads(msg if isinstance(msg, str) else msg.decode("utf-8"))
                return payload.get("seq")
            except (json.JSONDecodeError, KeyError, ValueError):
                return None

        async def on_gap(result) -> None:
            gaps.append(result.received)

        client = ReconnectingWebSocketClient(
            url,
            ping_interval=0,
            sequence_extractor=extract_seq,
            on_sequence_gap=on_gap,
            raise_on_sequence_gap=False,
        )

        async with client:
            await client.wait_connected(timeout=2.0)
            # Message 1
            await client.send(json.dumps({"seq": 1, "data": "first"}))
            # Gap: Message 3
            await client.send(json.dumps({"seq": 3, "data": "third"}))
            await asyncio.sleep(0.05)

            assert 3 in gaps
            assert client.sequence_tracker.gaps_detected == 1

    @pytest.mark.asyncio
    async def test_raise_on_sequence_gap(self, local_echo_server) -> None:
        url = local_echo_server["url"]
        error_caught = asyncio.Event()

        def extract_seq(msg: str | bytes) -> int | None:
            data = json.loads(msg if isinstance(msg, str) else msg.decode("utf-8"))
            return data.get("seq")

        async def on_err(exc: Exception) -> None:
            if isinstance(exc, SequenceGapError):
                error_caught.set()

        client = ReconnectingWebSocketClient(
            url,
            ping_interval=0,
            reconnect=False,
            sequence_extractor=extract_seq,
            raise_on_sequence_gap=True,
            on_error=on_err,
        )

        async with client:
            await client.wait_connected(timeout=2.0)
            await client.send(json.dumps({"seq": 10}))
            await client.send(json.dumps({"seq": 15}))  # Gap triggers error
            await asyncio.wait_for(error_caught.wait(), timeout=2.0)
            assert error_caught.is_set()

    @pytest.mark.asyncio
    async def test_max_reconnect_attempts_exceeded(self) -> None:
        # Port with nothing listening
        client = ReconnectingWebSocketClient(
            "ws://127.0.0.1:49999",
            reconnect=True,
            min_backoff=0.01,
            max_backoff=0.02,
            max_reconnect_attempts=2,
            ping_interval=0,
        )
        await client.start()
        await asyncio.sleep(0.15)
        await client.stop()
        assert client.reconnect_attempts >= 2

    @pytest.mark.asyncio
    async def test_reconnect_after_server_disconnect(self, local_echo_server) -> None:
        url = local_echo_server["url"]
        connections = 0

        async def on_conn() -> None:
            nonlocal connections
            connections += 1

        client = ReconnectingWebSocketClient(
            url,
            on_connected=on_conn,
            reconnect=True,
            min_backoff=0.02,
            max_backoff=0.05,
            ping_interval=0,
        )

        async with client:
            await client.wait_connected(timeout=2.0)
            assert connections == 1

            # Forcibly close server connection from server side
            active_clients = list(local_echo_server["clients"])
            for c in active_clients:
                await c.close()

            # Client should reconnect automatically
            await asyncio.sleep(0.2)
            assert client.is_connected
            assert connections >= 2

    @pytest.mark.asyncio
    async def test_custom_ping_payload(self, local_echo_server) -> None:
        url = local_echo_server["url"]
        pings_received = 0

        async def on_msg(data: str | bytes) -> None:
            nonlocal pings_received
            if data == "HONBA_PING":
                pings_received += 1

        client = ReconnectingWebSocketClient(
            url,
            ping_interval=0.05,
            ping_timeout=1.0,
            custom_ping_payload="HONBA_PING",
            on_message=on_msg,
        )

        async with client:
            await client.wait_connected(timeout=2.0)
            await asyncio.sleep(0.15)
            assert pings_received >= 2
