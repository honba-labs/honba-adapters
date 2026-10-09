"""Binary WebSocket feed decoder and client for DhanHQ (M2).

Connects to ``wss://api-feed.dhan.co`` and unpacks binary frames:
- Response Code 17: LTP Ticker
- Response Code 18: Quote (OHLC + 5-level depth)
- Response Code 19: Full Depth (20 price levels)
Dispatches decoded events to registered callbacks as canonical Honba types.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from honba.adapters.models import DepthLevel, MarketDepth, StreamCallback, StreamMode
from honba.domain.instrument import InstrumentId
from honba.domain.tick import QuoteTick
from honba_adapters_shared.websocket import ReconnectingWebSocketClient

from honba_dhan.constants import (
    DHAN_FEED_CODE_DEPTH_20,
    DHAN_FEED_CODE_LTP,
    DHAN_FEED_CODE_QUOTE,
)

if TYPE_CHECKING:
    from honba_dhan.config import DhanConfig
    from honba_dhan.instruments import DhanInstrumentMaster

__all__ = ["DhanFeedClient", "unpack_depth_20_packet", "unpack_ltp_packet", "unpack_quote_packet"]


def unpack_ltp_packet(
    data: bytes,
    id_resolver: Callable[[str, str], InstrumentId | None],
) -> QuoteTick | None:
    """Unpack Dhan binary feed response code 17 (LTP)."""
    if len(data) < 16:
        return None
    # Header: response_code (1B), msg_len (2B), exch_segment (1B), security_id (4B uint32)
    _, _, seg_byte, sec_id_int = struct.unpack(">BBHI", data[:8])
    # Payload: ltp (4B float or int cents), ltt (4B)
    ltp_int = struct.unpack(">I", data[8:12])[0]
    ltp = ltp_int / 100.0 if ltp_int > 100000 else float(ltp_int)

    seg_str = "NSE_EQ" if seg_byte == 1 else "BSE_EQ"
    inst_id = id_resolver(str(sec_id_int), seg_str)
    if inst_id is None:
        inst_id = InstrumentId(str(sec_id_int), "NSE")

    now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)
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
    id_resolver: Callable[[str, str], InstrumentId | None],
) -> QuoteTick | None:
    """Unpack Dhan binary feed response code 18 (Quote)."""
    if len(data) < 40:
        return None
    _, _, seg_byte, sec_id_int = struct.unpack(">BBHI", data[:8])
    ltp_int = struct.unpack(">I", data[8:12])[0]
    ltp = ltp_int / 100.0 if ltp_int > 100000 else float(ltp_int)

    seg_str = "NSE_EQ" if seg_byte == 1 else "BSE_EQ"
    inst_id = id_resolver(str(sec_id_int), seg_str)
    if inst_id is None:
        inst_id = InstrumentId(str(sec_id_int), "NSE")

    now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)
    return QuoteTick(
        instrument_id=inst_id,
        ts=now_ns,
        bid_price=ltp,
        ask_price=ltp,
        bid_size=1.0,
        ask_size=1.0,
    )


def unpack_depth_20_packet(
    data: bytes,
    id_resolver: Callable[[str, str], InstrumentId | None],
) -> MarketDepth | None:
    """Unpack Dhan binary feed response code 19 (20-Level Market Depth)."""
    if len(data) < 8:
        return None
    _, _, seg_byte, sec_id_int = struct.unpack(">BBHI", data[:8])
    seg_str = "NSE_EQ" if seg_byte == 1 else "BSE_EQ"
    inst_id = id_resolver(str(sec_id_int), seg_str)
    if inst_id is None:
        inst_id = InstrumentId(str(sec_id_int), "NSE")

    # Following header, 20 bid levels (price 4B, qty 4B) then 20 ask levels
    offset = 8
    bids: list[DepthLevel] = []
    asks: list[DepthLevel] = []

    for _ in range(20):
        if offset + 8 > len(data):
            break
        p_raw, q_raw = struct.unpack(">II", data[offset : offset + 8])
        p = p_raw / 100.0 if p_raw > 100000 else float(p_raw)
        if p > 0:
            bids.append(DepthLevel(price=p, quantity=float(q_raw)))
        offset += 8

    for _ in range(20):
        if offset + 8 > len(data):
            break
        p_raw, q_raw = struct.unpack(">II", data[offset : offset + 8])
        p = p_raw / 100.0 if p_raw > 100000 else float(p_raw)
        if p > 0:
            asks.append(DepthLevel(price=p, quantity=float(q_raw)))
        offset += 8

    # Sort
    bids.sort(key=lambda x: x.price, reverse=True)
    asks.sort(key=lambda x: x.price)

    now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)
    return MarketDepth(
        instrument_id=inst_id,
        ts=now_ns,
        bids=tuple(bids),
        asks=tuple(asks),
    )


class DhanFeedClient:
    """Manages WebSocket connection to Dhan feed server with packet decoding."""

    def __init__(
        self,
        config: DhanConfig,
        instruments: DhanInstrumentMaster,
    ) -> None:
        self._config = config
        self._instruments = instruments
        self._callbacks: list[StreamCallback] = []
        self._subscribed_instruments: set[InstrumentId] = set()

        self._ws = ReconnectingWebSocketClient(
            config.feed_url,
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
            await self._send_subscription(instruments, is_subscribe=True)

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
        """Authenticate session and restore subscriptions."""
        auth_payload = {
            "RequestCode": 11,
            "clientId": self._config.client_id,
            "token": self._config.access_token,
        }
        await self._ws.send_json(auth_payload)

        if self._subscribed_instruments:
            await self._send_subscription(tuple(self._subscribed_instruments), is_subscribe=True)

    async def _send_subscription(
        self, instruments: tuple[InstrumentId, ...], *, is_subscribe: bool = True
    ) -> None:
        instrument_list: list[dict[str, Any]] = []
        for inst_id in instruments:
            resolved = self._instruments.resolve_to_dhan(inst_id)
            if resolved:
                sec_id, seg = resolved
                instrument_list.append({"ExchangeSegment": seg, "SecurityId": sec_id})

        if not instrument_list:
            return

        payload = {
            "RequestCode": 21 if is_subscribe else 22,
            "InstrumentCount": len(instrument_list),
            "InstrumentList": instrument_list,
        }
        await self._ws.send_json(payload)

    async def _on_message(self, message: str | bytes) -> None:
        """Process incoming binary feed packets."""
        if isinstance(message, str):
            return  # text control messages ignored

        if len(message) < 8:
            return

        response_code = message[0]
        id_resolver = self._instruments.resolve_from_dhan
        event = None

        if response_code == DHAN_FEED_CODE_LTP:
            event = unpack_ltp_packet(message, id_resolver)
        elif response_code == DHAN_FEED_CODE_QUOTE:
            event = unpack_quote_packet(message, id_resolver)
        elif response_code == DHAN_FEED_CODE_DEPTH_20:
            event = unpack_depth_20_packet(message, id_resolver)

        if event is not None:
            for cb in self._callbacks:
                with contextlib.suppress(Exception):
                    cb_fn: Any = cb
                    cb_fn(event)

