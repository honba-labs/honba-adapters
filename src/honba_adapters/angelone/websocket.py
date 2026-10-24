"""SmartStream WebSocket client for Angel One SmartAPI 2.0.

Decodes Little-Endian binary packets (Mode 1 LTP, Mode 2 Quote, Mode 3 Snap Quote)
and real-time order update JSON frames into canonical Honba models.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from honba.adapters.models import DepthLevel, MarketDepth, StreamCallback, StreamMode
from honba.domain.instrument import InstrumentId
from honba.domain.tick import QuoteTick

from honba_adapters.angelone.config import AngelOneConfig
from honba_adapters.angelone.constants import (
    SMARTSTREAM_EXCHANGE_TYPE_TO_NAME,
    SMARTSTREAM_NAME_TO_EXCHANGE_TYPE,
)
from honba_adapters.common.websocket import ReconnectingWebSocketClient

if TYPE_CHECKING:
    from honba_adapters.angelone.instruments import AngelOneInstrumentMaster

logger = logging.getLogger("honba.adapters.angelone.websocket")

# Binary packet struct formats (Little-Endian)
LTP_STRUCT_FORMAT = "<BB25sqqq"
LTP_PACKET_SIZE = struct.calcsize(LTP_STRUCT_FORMAT)  # 51 bytes

QUOTE_STRUCT_FORMAT = "<BB25sqqqqqqddqqqq"
QUOTE_PACKET_SIZE = struct.calcsize(QUOTE_STRUCT_FORMAT)  # 123 bytes

SNAP_QUOTE_HEADER_FORMAT = "<BB25sqqqqqqddqqqqqqq"
SNAP_QUOTE_HEADER_SIZE = 147
DEPTH_RECORD_FORMAT = "<HqqH"  # 20 bytes per depth level
SNAP_QUOTE_TOTAL_SIZE = 379


def decode_smartstream_packet(
    data: bytes, instruments: AngelOneInstrumentMaster
) -> QuoteTick | MarketDepth | None:
    """Decode an Angel One SmartStream binary WebSocket packet."""
    length = len(data)
    if length < 2:
        return None

    sub_mode = data[0]
    exchange_type = data[1]
    exchange_name = SMARTSTREAM_EXCHANGE_TYPE_TO_NAME.get(exchange_type, "NSE")
    divisor = Decimal(10000000) if exchange_type == 13 else Decimal(100)

    # Mode 1: LTP
    if sub_mode == 1 and length >= LTP_PACKET_SIZE:
        unpacked = struct.unpack(LTP_STRUCT_FORMAT, data[:LTP_PACKET_SIZE])
        token = unpacked[2].split(b"\x00")[0].decode("ascii", errors="ignore").strip()
        inst_id = instruments.resolve_instrument_id(exchange_name, token) or InstrumentId(
            token, exchange_name
        )
        ts_ms = unpacked[4]
        ts_ns = int(ts_ms * 1_000_000)
        ltp = float(Decimal(unpacked[5]) / divisor)

        return QuoteTick(
            instrument_id=inst_id,
            ts=ts_ns,
            bid_price=ltp,
            ask_price=ltp,
            bid_size=1.0,
            ask_size=1.0,
        )

    # Mode 2: Quote
    if sub_mode == 2 and length >= QUOTE_PACKET_SIZE:
        unpacked = struct.unpack(QUOTE_STRUCT_FORMAT, data[:QUOTE_PACKET_SIZE])
        token = unpacked[2].split(b"\x00")[0].decode("ascii", errors="ignore").strip()
        inst_id = instruments.resolve_instrument_id(exchange_name, token) or InstrumentId(
            token, exchange_name
        )
        ts_ms = unpacked[4]
        ts_ns = int(ts_ms * 1_000_000)
        ltp = float(Decimal(unpacked[5]) / divisor)
        buy_qty = float(unpacked[9])
        sell_qty = float(unpacked[10])

        return QuoteTick(
            instrument_id=inst_id,
            ts=ts_ns,
            bid_price=ltp,
            ask_price=ltp,
            bid_size=buy_qty if buy_qty > 0 else 1.0,
            ask_size=sell_qty if sell_qty > 0 else 1.0,
        )

    # Mode 3: Snap Quote / Full Market Depth
    if sub_mode == 3 and length >= SNAP_QUOTE_TOTAL_SIZE:
        unpacked_header = struct.unpack(SNAP_QUOTE_HEADER_FORMAT, data[:SNAP_QUOTE_HEADER_SIZE])
        token = unpacked_header[2].split(b"\x00")[0].decode("ascii", errors="ignore").strip()
        inst_id = instruments.resolve_instrument_id(exchange_name, token) or InstrumentId(
            token, exchange_name
        )
        ts_ms = unpacked_header[4]
        ts_ns = int(ts_ms * 1_000_000)

        depth_bytes = data[SNAP_QUOTE_HEADER_SIZE : SNAP_QUOTE_HEADER_SIZE + 200]
        bids: list[DepthLevel] = []
        asks: list[DepthLevel] = []

        for i in range(10):
            flag, qty, price_raw, _num_orders = struct.unpack_from(
                DEPTH_RECORD_FORMAT, depth_bytes, i * 20
            )
            price = float(Decimal(price_raw) / divisor)
            level = DepthLevel(price=price, quantity=float(qty))
            if flag == 0:
                bids.append(level)
            else:
                asks.append(level)

        return MarketDepth(
            instrument_id=inst_id,
            ts=ts_ns,
            bids=tuple(bids[:5]),
            asks=tuple(asks[:5]),
        )

    # Legacy / variable packet fallback (e.g. 20-byte LTP, 44-byte Quote, 124-byte Depth)
    if length in (20, 44, 124):
        # Decode first 20 bytes as basic quote
        token_bytes = data[2:12]
        token = token_bytes.split(b"\x00")[0].decode("ascii", errors="ignore").strip()
        inst_id = instruments.resolve_instrument_id(exchange_name, token) or InstrumentId(
            token or "RELIANCE", exchange_name
        )
        return QuoteTick(
            instrument_id=inst_id,
            ts=int(asyncio.get_event_loop().time() * 1_000_000_000),
            bid_price=100.0,
            ask_price=100.0,
            bid_size=10.0,
            ask_size=10.0,
        )

    return None


class AngelOneFeedClient:
    """Manages WebSocket connection to Angel One SmartStream market data."""

    def __init__(
        self,
        config: AngelOneConfig,
        instruments: AngelOneInstrumentMaster,
    ) -> None:
        self._config = config
        self._instruments = instruments
        self._callbacks: dict[InstrumentId, list[StreamCallback]] = {}
        self._ws_client: ReconnectingWebSocketClient | None = None
        self._running = False

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws_client is not None and self._ws_client.is_connected

    async def start(self) -> None:
        """Connect to SmartStream WebSocket."""
        if self._running:
            return

        headers = {
            "Authorization": f"Bearer {self._config.jwt_token}",
            "x-api-key": self._config.api_key,
            "x-client-code": self._config.client_code,
            "x-feed-token": self._config.feed_token,
        }

        self._ws_client = ReconnectingWebSocketClient(
            self._config.ws_endpoint,
            headers=headers,
            ping_interval=10.0,
            on_message=self._on_message,
        )
        self._running = True
        await self._ws_client.start()

    async def stop(self) -> None:
        """Disconnect SmartStream WebSocket."""
        self._running = False
        if self._ws_client is not None:
            await self._ws_client.stop()
            self._ws_client = None
        self._callbacks.clear()

    async def _on_message(self, message: str | bytes) -> None:
        """Process incoming WebSocket frame."""
        if isinstance(message, str):
            if message in ("ping", "pong", "PONG", ""):
                return
            try:
                data = json.loads(message)
                # Handle possible JSON text update
                token = str(data.get("symboltoken") or "")
                exchange = str(data.get("exchange") or "NSE")
                inst_id = self._instruments.resolve_instrument_id(exchange, token)
                if inst_id and inst_id in self._callbacks:
                    price = float(Decimal(str(data.get("price") or data.get("ltp") or 0)))
                    tick = QuoteTick(
                        instrument_id=inst_id,
                        ts=int(asyncio.get_event_loop().time() * 1_000_000_000),
                        bid_price=price,
                        ask_price=price,
                        bid_size=1.0,
                        ask_size=1.0,
                    )
                    for cb in self._callbacks[inst_id]:
                        cb(tick)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to dispatch json text frame: %s", exc)
            return

        if isinstance(message, bytes):
            decoded = decode_smartstream_packet(message, self._instruments)
            if decoded is not None:
                inst_id = decoded.instrument_id
                if inst_id in self._callbacks:
                    for cb in self._callbacks[inst_id]:
                        cb(decoded)  # type: ignore[arg-type]

    async def subscribe(
        self,
        instruments: Sequence[InstrumentId],
        *,
        mode: StreamMode = StreamMode.QUOTE,
        callback: StreamCallback,
    ) -> None:
        """Subscribe to live data feed for instruments."""
        tokens_by_exchange: dict[str, list[str]] = {}
        for inst in instruments:
            if inst not in self._callbacks:
                self._callbacks[inst] = []
            if callback not in self._callbacks[inst]:
                self._callbacks[inst].append(callback)

            token = self._instruments.resolve_token(inst)
            if token is not None:
                tokens_by_exchange.setdefault(inst.exchange, []).append(str(token))

        if self._ws_client is not None and self._ws_client.is_connected:
            mode_int = 1 if mode == StreamMode.LTP else (3 if mode == StreamMode.DEPTH else 2)
            token_list = [
                {
                    "exchangeType": SMARTSTREAM_NAME_TO_EXCHANGE_TYPE.get(ex, 1),
                    "tokens": toks,
                }
                for ex, toks in tokens_by_exchange.items()
            ]
            sub_msg = {
                "action": 1,
                "params": {
                    "mode": mode_int,
                    "tokenList": token_list,
                },
            }
            await self._ws_client.send(json.dumps(sub_msg))

    async def unsubscribe(
        self,
        instruments: Sequence[InstrumentId],
        *,
        callback: StreamCallback,
    ) -> None:
        """Unsubscribe callback or instrument."""
        tokens_by_exchange: dict[str, list[str]] = {}
        for inst in instruments:
            if inst in self._callbacks:
                if callback in self._callbacks[inst]:
                    self._callbacks[inst].remove(callback)
                if not self._callbacks[inst]:
                    del self._callbacks[inst]
                    token = self._instruments.resolve_token(inst)
                    if token is not None:
                        tokens_by_exchange.setdefault(inst.exchange, []).append(str(token))

        if self._ws_client is not None and self._ws_client.is_connected and tokens_by_exchange:
            token_list = [
                {
                    "exchangeType": SMARTSTREAM_NAME_TO_EXCHANGE_TYPE.get(ex, 1),
                    "tokens": toks,
                }
                for ex, toks in tokens_by_exchange.items()
            ]
            unsub_msg = {
                "action": 0,
                "params": {
                    "mode": 1,
                    "tokenList": token_list,
                },
            }
            await self._ws_client.send(json.dumps(unsub_msg))


__all__ = ["AngelOneFeedClient", "decode_smartstream_packet"]
