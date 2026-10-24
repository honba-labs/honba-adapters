"""Reconnecting asynchronous WebSocket transport and sequence tracker (M1).

Provides:
- State-tracked WebSocket client with exponential backoff and jitter.
- Periodic heartbeat monitoring (protocol ping/pong or custom broker payload).
- Message sequence tracking and gap detection.
- Subscription state retention for automatic resubmission on reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Any, Self

import websockets

from honba_adapters.common.errors import SequenceGapError, WebSocketError

logger = logging.getLogger("honba.adapters.shared.websocket")

__all__ = [
    "ReconnectingWebSocketClient",
    "SequenceCheckResult",
    "SequenceTracker",
    "WebSocketState",
    "websockets",
]


class WebSocketState(str, Enum):
    """Lifecycle states of the websocket transport."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SequenceCheckResult:
    """Result of evaluating a received sequence number against expected order."""

    is_gap: bool
    is_duplicate: bool
    expected: int
    received: int
    gap_size: int = 0


class SequenceTracker:
    """Tracks sequence numbers to identify message drops, gaps, and duplicate frames."""

    def __init__(self, initial_sequence: int | None = None) -> None:
        self._next_expected: int | None = initial_sequence
        self._last_received: int | None = None
        self._gaps_detected: int = 0
        self._duplicates_detected: int = 0

    @property
    def next_expected(self) -> int | None:
        return self._next_expected

    @property
    def last_received(self) -> int | None:
        return self._last_received

    @property
    def gaps_detected(self) -> int:
        return self._gaps_detected

    @property
    def duplicates_detected(self) -> int:
        return self._duplicates_detected

    def reset(self, initial_sequence: int | None = None) -> None:
        """Reset sequence tracker state."""
        self._next_expected = initial_sequence
        self._last_received = None
        self._gaps_detected = 0
        self._duplicates_detected = 0

    def track(self, seq: int) -> SequenceCheckResult:
        """Process an incoming sequence number and evaluate order.

        Args:
            seq: Incoming integer sequence number.

        Returns:
            SequenceCheckResult indicating if a gap or duplicate was detected.
        """
        self._last_received = seq

        if self._next_expected is None:
            # First sequence seen
            self._next_expected = seq + 1
            return SequenceCheckResult(
                is_gap=False,
                is_duplicate=False,
                expected=seq,
                received=seq,
                gap_size=0,
            )

        expected = self._next_expected

        if seq == expected:
            self._next_expected = seq + 1
            return SequenceCheckResult(
                is_gap=False,
                is_duplicate=False,
                expected=expected,
                received=seq,
                gap_size=0,
            )

        if seq > expected:
            gap_size = seq - expected
            self._gaps_detected += 1
            # Advance expected sequence past the gap
            self._next_expected = seq + 1
            return SequenceCheckResult(
                is_gap=True,
                is_duplicate=False,
                expected=expected,
                received=seq,
                gap_size=gap_size,
            )

        # seq < expected (duplicate or out-of-order)
        self._duplicates_detected += 1
        return SequenceCheckResult(
            is_gap=False,
            is_duplicate=True,
            expected=expected,
            received=seq,
            gap_size=0,
        )


class ReconnectingWebSocketClient:
    """Resilient WebSocket transport with reconnection, heartbeats, and gap detection."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        ping_interval: float = 30.0,
        ping_timeout: float = 10.0,
        reconnect: bool = True,
        min_backoff: float = 1.0,
        max_backoff: float = 30.0,
        backoff_factor: float = 2.0,
        jitter: float = 0.1,
        max_reconnect_attempts: int | None = None,
        sequence_extractor: Callable[[str | bytes], int | None] | None = None,
        raise_on_sequence_gap: bool = False,
        custom_ping_payload: str | bytes | None = None,
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_message: Callable[[str | bytes], Awaitable[None]] | None = None,
        on_error: Callable[[Exception], Awaitable[None]] | None = None,
        on_disconnected: Callable[[int | None, str | None], Awaitable[None]] | None = None,
        on_sequence_gap: Callable[[SequenceCheckResult], Awaitable[None]] | None = None,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._reconnect = reconnect
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._backoff_factor = backoff_factor
        self._jitter = jitter
        self._max_reconnect_attempts = max_reconnect_attempts

        self._sequence_extractor = sequence_extractor
        self._raise_on_sequence_gap = raise_on_sequence_gap
        self._custom_ping_payload = custom_ping_payload

        self._on_connected = on_connected
        self._on_message = on_message
        self._on_error = on_error
        self._on_disconnected = on_disconnected
        self._on_sequence_gap = on_sequence_gap

        self._state: WebSocketState = WebSocketState.DISCONNECTED
        self._ws: Any = None
        self._runner_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._sequence_tracker = SequenceTracker()
        self._subscriptions: set[Any] = set()
        self._reconnect_attempts: int = 0

    @property
    def url(self) -> str:
        return self._url

    @property
    def state(self) -> WebSocketState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == WebSocketState.CONNECTED and self._ws is not None

    @property
    def sequence_tracker(self) -> SequenceTracker:
        return self._sequence_tracker

    @property
    def subscriptions(self) -> frozenset[Any]:
        return frozenset(self._subscriptions)

    @property
    def reconnect_attempts(self) -> int:
        return self._reconnect_attempts

    def halt_reconnects(self) -> None:
        """Stop retrying after the next disconnect (e.g. the server rejected the credentials)."""
        self._reconnect = False

    @property
    def reconnects_enabled(self) -> bool:
        return self._reconnect

    def add_subscriptions(self, *items: Any) -> None:
        """Register subscriptions to be retained across reconnections."""
        self._subscriptions.update(items)

    def remove_subscriptions(self, *items: Any) -> None:
        """Deregister subscriptions."""
        self._subscriptions.difference_update(items)

    def clear_subscriptions(self) -> None:
        """Clear all registered subscriptions."""
        self._subscriptions.clear()

    async def start(self) -> None:
        """Start the background transport runner task."""
        if self._runner_task is not None and not self._runner_task.done():
            return
        self._stop_event.clear()
        self._runner_task = asyncio.create_task(self._run_loop(), name="ws_transport_runner")

    async def stop(self) -> None:
        """Gracefully stop the websocket transport and background tasks."""
        self._state = WebSocketState.CLOSING
        self._stop_event.set()
        self._connected_event.clear()

        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

        if self._runner_task is not None:
            self._runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None

        self._state = WebSocketState.CLOSED

    async def wait_connected(self, timeout: float | None = None) -> None:
        """Wait until connection is established."""
        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)

    async def send(self, data: str | bytes) -> None:
        """Send raw text or binary frame over the active connection."""
        if not self.is_connected or self._ws is None:
            raise WebSocketError("Cannot send: WebSocket is not connected")
        try:
            await self._ws.send(data)
        except Exception as e:
            raise WebSocketError(f"Failed to send data: {e}") from e

    async def send_json(self, payload: Any) -> None:
        """Serialize payload to JSON and send as text frame."""
        await self.send(json.dumps(payload))

    async def _run_loop(self) -> None:
        """Main connection and reconnection lifecycle loop."""
        backoff = self._min_backoff

        while not self._stop_event.is_set():
            self._state = (
                WebSocketState.CONNECTING
                if self._reconnect_attempts == 0
                else WebSocketState.RECONNECTING
            )

            try:
                # Establish WebSocket connection
                extra_headers = self._headers if self._headers else None
                connect_kwargs: dict[str, Any] = {}
                if extra_headers:
                    connect_kwargs["additional_headers"] = extra_headers

                async with websockets.connect(self._url, **connect_kwargs) as ws:
                    self._ws = ws
                    self._state = WebSocketState.CONNECTED
                    self._reconnect_attempts = 0
                    backoff = self._min_backoff
                    self._connected_event.set()

                    if self._on_connected is not None:
                        try:
                            await self._on_connected()
                        except Exception:
                            logger.exception("Error in on_connected callback")

                    active_tasks: list[asyncio.Task[Any]] = []
                    if self._ping_interval > 0:
                        active_tasks.append(asyncio.create_task(self._heartbeat_loop(ws)))
                    receiver_task = asyncio.create_task(self._receiver_loop(ws))
                    active_tasks.append(receiver_task)

                    done, pending = await asyncio.wait(
                        active_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for task in pending:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task

                    # Raise any non-cancellation exception from completed task
                    for task in done:
                        if not task.cancelled() and task.exception() is not None:
                            exc = task.exception()
                            assert exc is not None
                            raise exc

                    # If stopped gracefully, break
                    if self._stop_event.is_set():
                        break

                    # If receiver finished without error but stop_event not set, remote closed connection
                    raise WebSocketError("Remote connection closed")

            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self._connected_event.clear()
                self._ws = None
                if self._stop_event.is_set():
                    break

                if self._on_error is not None:
                    with contextlib.suppress(Exception):
                        await self._on_error(e)

                if self._on_disconnected is not None:
                    with contextlib.suppress(Exception):
                        await self._on_disconnected(None, str(e))

                if not self._reconnect:
                    self._state = WebSocketState.DISCONNECTED
                    break

                self._reconnect_attempts += 1
                if (
                    self._max_reconnect_attempts is not None
                    and self._reconnect_attempts > self._max_reconnect_attempts
                ):
                    self._state = WebSocketState.DISCONNECTED
                    logger.error(
                        "Max reconnect attempts (%d) reached", self._max_reconnect_attempts
                    )
                    break

                # Exponential backoff with jitter
                jitter_val = random.uniform(-self._jitter, self._jitter) * backoff
                sleep_duration = max(0.01, min(self._max_backoff, backoff + jitter_val))
                backoff = min(self._max_backoff, backoff * self._backoff_factor)

                try:
                    await asyncio.sleep(sleep_duration)
                except asyncio.CancelledError:
                    break

        self._state = (
            WebSocketState.CLOSED if self._stop_event.is_set() else WebSocketState.DISCONNECTED
        )

    async def _heartbeat_loop(self, ws: Any) -> None:
        """Periodically sends heartbeats and verifies connection responsiveness."""
        if self._ping_interval <= 0:
            return

        while not self._stop_event.is_set():
            await asyncio.sleep(self._ping_interval)
            try:
                if self._custom_ping_payload is not None:
                    await ws.send(self._custom_ping_payload)
                else:
                    # Native protocol ping frame
                    pong_waiter = await ws.ping()
                    await asyncio.wait_for(pong_waiter, timeout=self._ping_timeout)
            except Exception as e:
                logger.warning("WebSocket heartbeat failed: %s", e)
                raise WebSocketError(f"Heartbeat timeout / failure: {e}") from e

    async def _receiver_loop(self, ws: Any) -> None:
        """Continuously consumes frames from the websocket."""
        async for raw_message in ws:
            # Process sequence if extractor provided
            if self._sequence_extractor is not None:
                seq = self._sequence_extractor(raw_message)
                if seq is not None:
                    result = self._sequence_tracker.track(seq)
                    if result.is_gap:
                        if self._on_sequence_gap is not None:
                            try:
                                await self._on_sequence_gap(result)
                            except Exception:
                                logger.exception("Error in on_sequence_gap callback")
                        if self._raise_on_sequence_gap:
                            raise SequenceGapError(
                                f"Sequence gap: expected {result.expected}, got {result.received}",
                                expected=result.expected,
                                received=result.received,
                            )

            if self._on_message is not None:
                try:
                    await self._on_message(raw_message)
                except Exception:
                    logger.exception("Error in on_message callback")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.stop()
