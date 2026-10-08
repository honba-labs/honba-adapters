"""Binary WebSocket feed decoder and client for Zerodha KiteTicker (M3).

Connects to ``wss://ws.kite.trade`` and unpacks KiteTicker binary frames:
- 8 bytes: LTP Mode
- 44 bytes: Quote Mode (OHLC + Volume)
- 184 bytes: Full Market Depth Mode (5-level order book)
Dispatches decoded events to registered callbacks as canonical Honba types.
"""

from __future__ import annotations

import contextlib
import struct
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from honba.adapters.models import DepthLevel, MarketDepth, StreamCallback, StreamMode
from honba.domain.instrument import InstrumentId
from honba.domain.tick import QuoteTick
from honba_adapters_shared.websocket import ReconnectingWebSocketClient

from honba_zerodha.constants import ZERODHA_STREAM_MODE_MAP

if TYPE_CHECKING:
    from honba_zerodha.config import ZerodhaConfig
    from honba_zerodha.instruments import ZerodhaInstrumentMaster

__all__ = [
    "ZerodhaFeedClient",
    "unpack_full_depth_packet",
    "unpack_kite_packet",
    "unpack_ltp_packet",
    "unpack_quote_packet",
]


def unpack_ltp_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
) -> QuoteTick | None:
    """Unpack KiteTicker 8-byte LTP packet."""
    if len(data) < 8:
        return None
    token, ltp_int = struct.unpack(">II", data[:8])
    ltp = ltp_int / 100.0

    inst_id = id_resolver(token)
    if inst_id is None:
        inst_id = InstrumentId(str(token), "NSE")

    now_ns = time.time_ns()
    return QuoteTick(
        instrument_id=inst_id,
        ts=now_ns,
        bid_price=ltp,
        ask_price=ltp,
        bid_size=1.0,
        ask_size=1.0,
    )


def unpack_quote_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
) -> QuoteTick | None:
    """Unpack KiteTicker 44-byte Quote packet."""
    if len(data) < 44:
        return None
    # >I i I i I I I i i i i
    (
        token,
        ltp_int,
        _last_qty,
        _avg_price,
        _volume,
        buy_qty,
        sell_qty,
        _open,
        _high,
        _low,
        _close,
    ) = struct.unpack(">IiIiIIIiiii", data[:44])

    ltp = ltp_int / 100.0
    inst_id = id_resolver(token)
    if inst_id is None:
        inst_id = InstrumentId(str(token), "NSE")

    now_ns = time.time_ns()
    return QuoteTick(
        instrument_id=inst_id,
        ts=now_ns,
        bid_price=ltp,
        ask_price=ltp,
        bid_size=float(buy_qty),
        ask_size=float(sell_qty),
    )


def unpack_full_depth_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
) -> MarketDepth | None:
    """Unpack KiteTicker 184-byte Full Depth packet."""
    if len(data) < 184:
        return None

    token, _ltp_int = struct.unpack(">Ii", data[:8])

    inst_id = id_resolver(token)
    if inst_id is None:
        inst_id = InstrumentId(str(token), "NSE")

    bids: list[DepthLevel] = []
    asks: list[DepthLevel] = []

    # Bids: 5 levels starting at byte offset 64, each 12 bytes: qty (4B), price (4B), orders (2B), padding (2B)
    offset = 64
    for _ in range(5):
        qty, price_raw = struct.unpack(">Ii", data[offset : offset + 8])
        price = price_raw / 100.0
        if price > 0:
            bids.append(DepthLevel(price=price, quantity=float(qty)))
        offset += 12

    # Asks: 5 levels starting at byte offset 124, each 12 bytes
    offset = 124
    for _ in range(5):
        qty, price_raw = struct.unpack(">Ii", data[offset : offset + 8])
        price = price_raw / 100.0
        if price > 0:
            asks.append(DepthLevel(price=price, quantity=float(qty)))
        offset += 12

    now_ns = time.time_ns()
    return MarketDepth(
        instrument_id=inst_id,
        ts=now_ns,
        bids=tuple(bids),
        asks=tuple(asks),
    )


def unpack_kite_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
) -> list[QuoteTick | MarketDepth]:
    """Unpack a composite KiteTicker binary frame containing multiple packets."""
    if len(data) < 2:
        return []

    num_packets = struct.unpack(">H", data[:2])[0]
    offset = 2
    events: list[QuoteTick | MarketDepth] = []

    for _ in range(num_packets):
        if offset + 2 > len(data):
            break
        packet_len = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
        if offset + packet_len > len(data):
            break

        packet_data = data[offset : offset + packet_len]
        offset += packet_len

        if packet_len == 8:
            tick = unpack_ltp_packet(packet_data, id_resolver)
            if tick is not None:
                events.append(tick)
        elif packet_len == 44:
            quote = unpack_quote_packet(packet_data, id_resolver)
            if quote is not None:
                events.append(quote)
        elif packet_len == 184:
            depth = unpack_full_depth_packet(packet_data, id_resolver)
            if depth is not None:
                events.append(depth)

    return events


class ZerodhaFeedClient:
    """Manages WebSocket connection to KiteTicker server with packet decoding."""

    def __init__(
        self,
        config: ZerodhaConfig,
        instruments: ZerodhaInstrumentMaster,
    ) -> None:
        self._config = config
        self._instruments = instruments
        self._callbacks: list[StreamCallback] = []
        self._subscribed_instruments: set[InstrumentId] = set()

        ws_url = (
            f"{config.ws_endpoint.rstrip('/')}/?api_key={config.api_key}"
            f"&access_token={config.access_token}"
        )
        self._ws = ReconnectingWebSocketClient(
            ws_url,
            ping_interval=20.0,
            on_connected=self._on_connected,
            on_message=self._on_message,
        )

    @property
    def is_connected(self) -> bool:
        return self._ws.is_connected

    async def start(self) -> None:
        await self._ws.start()

    async def stop(self) -> None:
        await self._ws.stop()

    def add_callback(self, callback: StreamCallback) -> None:
        self._callbacks.append(callback)

    def remove_callback(self, callback: StreamCallback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def subscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        mode: StreamMode = StreamMode.QUOTE,
        callback: StreamCallback | None = None,
    ) -> None:
        if callback is not None:
            self.add_callback(callback)
        self._subscribed_instruments.update(instruments)

        if self._ws.is_connected:
            await self._send_subscription(instruments, mode=mode, is_subscribe=True)

    async def unsubscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        callback: StreamCallback | None = None,
    ) -> None:
        if callback is not None:
            self.remove_callback(callback)
        self._subscribed_instruments.difference_update(instruments)

        if self._ws.is_connected:
            await self._send_subscription(instruments, is_subscribe=False)

    async def _on_connected(self) -> None:
        """Restore subscriptions upon WebSocket connection establishment."""
        if self._subscribed_instruments:
            await self._send_subscription(
                tuple(self._subscribed_instruments),
                mode=StreamMode.QUOTE,
                is_subscribe=True,
            )

    async def _send_subscription(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        mode: StreamMode = StreamMode.QUOTE,
        is_subscribe: bool = True,
    ) -> None:
        tokens: list[int] = []
        for inst in instruments:
            token = self._instruments.resolve_token(inst)
            if token is not None:
                tokens.append(token)

        if not tokens:
            return

        if is_subscribe:
            await self._ws.send_json({"a": "subscribe", "v": tokens})
            mode_str = ZERODHA_STREAM_MODE_MAP.get(mode, "quote")
            await self._ws.send_json({"a": "mode", "v": [mode_str, tokens]})
        else:
            await self._ws.send_json({"a": "unsubscribe", "v": tokens})

    async def _on_message(self, message: str | bytes) -> None:
        """Handle incoming binary or text frames from KiteTicker."""
        if isinstance(message, bytes):
            events = unpack_kite_packet(message, self._instruments.resolve_instrument_id)
            for event in events:
                for cb in list(self._callbacks):
                    with contextlib.suppress(Exception):
                        res = cb(event)
                        if hasattr(res, "__await__"):
                            await res
