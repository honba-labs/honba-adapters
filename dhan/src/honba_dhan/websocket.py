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
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from honba.adapters.errors import AdapterError, AdapterFatalError
from honba.adapters.models import DepthLevel, MarketDepth, StreamCallback, StreamMode
from honba.domain.instrument import InstrumentId
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick
from honba_adapters_shared.errors import AuthenticationError
from honba_adapters_shared.websocket import ReconnectingWebSocketClient

from honba_dhan.constants import (
    DHAN_FEED_AUTH_DISCONNECT_CODES,
    DHAN_FEED_CODE_DEPTH_20,
    DHAN_FEED_CODE_LTP,
    DHAN_FEED_CODE_QUOTE,
    DHAN_FEED_V2_DISCONNECT,
    DHAN_FEED_V2_FULL,
    DHAN_FEED_V2_QUOTE,
    DHAN_FEED_V2_REQUEST,
    DHAN_FEED_V2_SEGMENTS,
    DHAN_FEED_V2_TICKER,
)

logger = logging.getLogger("honba_dhan.feed")

if TYPE_CHECKING:
    from honba_dhan.config import DhanConfig
    from honba_dhan.instruments import DhanInstrumentMaster

__all__ = [
    "DhanFeedClient",
    "FeedTick",
    "is_auth_rejection",
    "unpack_depth_20_packet",
    "unpack_ltp_packet",
    "unpack_quote_packet",
    "unpack_v2_packet",
]


def is_auth_rejection(exc: BaseException) -> bool:
    """True if ``exc`` is a websocket upgrade rejected with HTTP 401/403 (bad/expired token).

    ``websockets`` >= 14 raises ``InvalidStatus`` (``.response.status_code``); older versions
    raise ``InvalidStatusCode`` (``.status_code``). Both are handled without importing either.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status in (401, 403)


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


# --------------------------------------------------------------------------- feed v2 decoding
#
# Dhan market feed v2 (https://dhanhq.co/docs/v2/live-market-feed/): all numbers are
# LITTLE-ENDIAN; prices are float32; auth is via the connection URL, not a message.
#   header (8B)  : code u8, msg_len u16, segment u8, security_id u32
#   ticker  (2)  : ltp f32, ltt u32 (epoch s)                                  -> 16 B
#   quote   (4)  : ltp f32, ltq u16, ltt u32, atp f32, volume u32 (cumulative day),
#                  sell_qty u32, buy_qty u32, open f32, close f32, high f32, low f32 -> 50 B
#   full    (8)  : as quote but after buy_qty: oi u32, oi_hi u32, oi_lo u32, open, close,
#                  high, low (f32), then 5 depth levels x 20 B (bid_qty u32, ask_qty u32,
#                  bid_orders u16, ask_orders u16, bid_price f32, ask_price f32) -> 162 B
_V2_HEADER = struct.Struct("<BHBI")
_V2_TICKER = struct.Struct("<fI")
_V2_QUOTE = struct.Struct("<fHIfIIIffff")
_V2_FULL_HEAD = struct.Struct("<fHIfIIIIIIffff")
_V2_DEPTH_LEVEL = struct.Struct("<IIHHff")
_V2_MIN_LEN = {DHAN_FEED_V2_TICKER: 16, DHAN_FEED_V2_QUOTE: 50, DHAN_FEED_V2_FULL: 162}

# LTT offset. Docs (live-market-feed) only say "EPOCH" with no timezone. The official dhanhq
# client formats LTT with datetime.fromtimestamp(ltt, UTC) as HH:MM:SS, which implies exchange
# wall-clock (IST) encoded as if UTC, i.e. a +19800 s shift; but that is indirect and no doc says
# so. Kept at 0 (plain epoch) until confirmed against a live session. See README.
LTT_EPOCH_OFFSET_S = 0


@dataclass(frozen=True, slots=True)
class FeedTick:
    """One decoded last-trade update; ``cum_volume`` is the cumulative day volume if known."""

    instrument_id: InstrumentId
    ts: int  # unix nanoseconds, exchange last-trade time (receive time if absent)
    price: float
    cum_volume: int | None
    last_qty: int = 0


def _px(value: float, segment: str) -> float:
    """float32 wire price -> clean float (paise; 4 dp for currency segments)."""
    return round(float(value), 4 if "CURRENCY" in segment else 2)


def unpack_v2_packet(
    data: bytes,
    id_resolver: Callable[[str, str], InstrumentId | None],
    *,
    now_ns: int | None = None,
) -> tuple[FeedTick, QuoteTick | None] | None:
    """Decode a v2 ticker/quote/full packet. Returns ``None`` for other or malformed frames.

    Exchange time comes from the packet's LTT; the local clock (``now_ns``) is only a fallback
    when LTT is 0. Ticker/quote packets carry no top of book, so the paired ``QuoteTick`` is
    only produced for full packets (real best bid/ask).
    """
    if len(data) < _V2_HEADER.size:
        return None
    code, _length, seg_byte, sec_id = _V2_HEADER.unpack_from(data, 0)
    need = _V2_MIN_LEN.get(code)
    if need is None or len(data) < need:
        return None
    segment = DHAN_FEED_V2_SEGMENTS.get(seg_byte, "NSE_EQ")
    inst_id = id_resolver(str(sec_id), segment) or InstrumentId(str(sec_id), "NSE")
    off = _V2_HEADER.size
    if now_ns is None:
        now_ns = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000_000)

    quote: QuoteTick | None = None
    if code == DHAN_FEED_V2_TICKER:
        ltp, ltt = _V2_TICKER.unpack_from(data, off)
        vol: int | None = None
        ltq = 0
    elif code == DHAN_FEED_V2_QUOTE:
        ltp, ltq, ltt, _atp, vol, *_rest = _V2_QUOTE.unpack_from(data, off)
    else:
        ltp, ltq, ltt, _atp, vol, *_rest = _V2_FULL_HEAD.unpack_from(data, off)
        # best (first) of the 5 depth levels follows the fixed head
        depth_off = off + _V2_FULL_HEAD.size
        bq, aq, _bo, _ao, bp, ap = _V2_DEPTH_LEVEL.unpack_from(data, depth_off)
        if bp > 0 and ap > 0 and bp <= ap:
            quote = QuoteTick(
                instrument_id=inst_id,
                ts=0,  # patched below once ts is known
                bid_price=_px(bp, segment),
                ask_price=_px(ap, segment),
                bid_size=float(bq),
                ask_size=float(aq),
            )

    ts = (int(ltt) + LTT_EPOCH_OFFSET_S) * 1_000_000_000 if ltt > 0 else now_ns
    if quote is not None:
        quote = QuoteTick(
            instrument_id=quote.instrument_id,
            ts=ts,
            bid_price=quote.bid_price,
            ask_price=quote.ask_price,
            bid_size=quote.bid_size,
            ask_size=quote.ask_size,
        )
    tick = FeedTick(inst_id, ts, _px(ltp, segment), vol, int(ltq))
    return tick, quote


class DhanFeedClient:
    """Manages the WebSocket connection to Dhan's feed (v2) with packet decoding.

    Callbacks receive ``QuoteTick`` (full/depth mode) and ``TradeTick`` (last trade) events,
    filtered to the instruments each subscription asked for. Internal listeners (e.g. the bar
    aggregator) can additionally receive raw :class:`FeedTick` objects, which carry the
    cumulative day volume that ``TradeTick`` cannot.
    """

    def __init__(
        self,
        config: DhanConfig,
        instruments: DhanInstrumentMaster,
        *,
        ws: Any | None = None,
        max_reconnects: int | None = None,
    ) -> None:
        self._config = config
        self._failure: AdapterError | None = None
        self._max_reconnects = (
            max_reconnects if max_reconnects is not None else config.feed_max_reconnects
        )
        self._instruments = instruments
        # (instruments, mode, callback) per subscription; unfiltered add_callback uses None.
        self._listeners: list[
            tuple[frozenset[InstrumentId] | None, StreamMode, StreamCallback]
        ] = []
        self._tick_listeners: list[Callable[[FeedTick], None]] = []
        self._subscribed: dict[InstrumentId, StreamMode] = {}

        self._ws: Any = ws or ReconnectingWebSocketClient(
            self._build_url(config),
            ping_interval=20.0,
            min_backoff=config.feed_min_backoff,
            max_backoff=config.feed_max_backoff,
            max_reconnect_attempts=self._max_reconnects,
            on_connected=self._on_connected,
            on_message=self._on_message,
            on_error=self._on_error,
        )
        self._connected_once = False
        self._consecutive_errors = 0
        self._reconnect_listeners: list[Callable[[], None]] = []

    @staticmethod
    def _build_url(config: DhanConfig) -> str:
        """v2 authenticates in the URL. NEVER log this value: it embeds the access token."""
        url = config.feed_url
        if "token=" in url:
            return url
        q = urlencode(
            {
                "version": 2,
                "token": config.access_token,
                "clientId": config.client_id,
                "authType": 2,
            }
        )
        return f"{url}{'&' if '?' in url else '?'}{q}"

    def __repr__(self) -> str:
        return f"DhanFeedClient(connected={self.is_connected}, subscribed={len(self._subscribed)})"

    @property
    def is_connected(self) -> bool:
        return bool(self._ws.is_connected)

    @property
    def failure(self) -> AdapterError | None:
        """Terminal feed failure (rejected token, or reconnect budget exhausted), else None."""
        return self._failure

    def raise_if_failed(self) -> None:
        err = self.failure
        if err is not None:
            raise err

    def add_reconnect_listener(self, listener: Callable[[], None]) -> None:
        """Called after every RE-connect (not the first connect): used to backfill the gap."""
        self._reconnect_listeners.append(listener)

    def _fail_auth(self) -> None:
        from honba_dhan.http import DH902_MESSAGE

        if self._failure is None:
            self._failure = AuthenticationError(DH902_MESSAGE)
        halt = getattr(self._ws, "halt_reconnects", None)
        if halt is not None:
            halt()

    async def _on_error(self, exc: Exception) -> None:
        if is_auth_rejection(exc):
            logger.error("Dhan feed upgrade rejected (auth); not reconnecting")
            self._fail_auth()
            return
        self._consecutive_errors += 1
        logger.warning("Dhan feed error: %s", type(exc).__name__)
        limit = self._max_reconnects
        # The shared client gives up once attempts > max; mirror that so callers see why.
        if limit is not None and self._consecutive_errors > limit and self._failure is None:
            self._failure = AdapterFatalError(
                f"Dhan feed gave up after {limit} consecutive reconnect attempts; "
                "check connectivity or raise feed_max_reconnects"
            )

    async def start(self) -> None:
        await self._ws.start()

    async def stop(self) -> None:
        await self._ws.stop()

    def add_callback(self, callback: StreamCallback) -> None:
        self._listeners.append((None, StreamMode.QUOTE, callback))

    def remove_callback(self, callback: StreamCallback) -> None:
        self._listeners = [x for x in self._listeners if x[2] != callback]

    def add_tick_listener(self, listener: Callable[[FeedTick], None]) -> None:
        self._tick_listeners.append(listener)

    def remove_tick_listener(self, listener: Callable[[FeedTick], None]) -> None:
        if listener in self._tick_listeners:
            self._tick_listeners.remove(listener)

    async def subscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        mode: StreamMode = StreamMode.QUOTE,
        callback: StreamCallback | None = None,
    ) -> None:
        if callback is not None:
            self._listeners.append((frozenset(instruments), mode, callback))
        for inst in instruments:
            self._subscribed[inst] = mode
        if self._ws.is_connected:
            await self._send_subscription(instruments, mode, is_subscribe=True)

    async def unsubscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        callback: StreamCallback | None = None,
    ) -> None:
        if callback is not None:
            self._listeners = [x for x in self._listeners if x[2] != callback]
        # Keep wire subscriptions that another listener still wants.
        still_wanted: set[InstrumentId] = set()
        for insts, _m, _cb in self._listeners:
            if insts is not None:
                still_wanted |= insts
        gone = tuple(i for i in instruments if i not in still_wanted)
        for inst in gone:
            mode = self._subscribed.pop(inst, StreamMode.QUOTE)
            if self._ws.is_connected:
                await self._send_subscription((inst,), mode, is_subscribe=False)

    async def _on_connected(self) -> None:
        """Restore subscriptions after (re)connect; v2 needs no auth message."""
        self._consecutive_errors = 0
        if self._connected_once:
            for listener in list(self._reconnect_listeners):
                with contextlib.suppress(Exception):
                    listener()
        self._connected_once = True
        by_mode: dict[StreamMode, list[InstrumentId]] = {}
        for inst, mode in self._subscribed.items():
            by_mode.setdefault(mode, []).append(inst)
        for mode, insts in by_mode.items():
            await self._send_subscription(tuple(insts), mode, is_subscribe=True)

    async def _send_subscription(
        self, instruments: tuple[InstrumentId, ...], mode: StreamMode, *, is_subscribe: bool
    ) -> None:
        instrument_list: list[dict[str, Any]] = []
        for inst_id in instruments:
            resolved = self._instruments.resolve_to_dhan(inst_id)
            if resolved:
                sec_id, seg = resolved
                instrument_list.append({"ExchangeSegment": seg, "SecurityId": sec_id})
        if not instrument_list:
            return
        sub_code, unsub_code = DHAN_FEED_V2_REQUEST[mode.value]
        # Dhan limits one request to 100 instruments.
        for i in range(0, len(instrument_list), 100):
            chunk = instrument_list[i : i + 100]
            await self._ws.send_json(
                {
                    "RequestCode": sub_code if is_subscribe else unsub_code,
                    "InstrumentCount": len(chunk),
                    "InstrumentList": chunk,
                }
            )

    def _dispatch(self, event: QuoteTick | TradeTick | Any) -> None:
        inst = event.instrument_id
        for insts, _mode, cb in list(self._listeners):
            if insts is None or inst in insts:
                with contextlib.suppress(Exception):
                    cb_fn: Any = cb
                    cb_fn(event)

    async def _on_message(self, message: str | bytes) -> None:
        """Process incoming binary feed packets."""
        if isinstance(message, str) or len(message) < 8:
            return  # text control messages ignored

        code = message[0]
        if code == DHAN_FEED_V2_DISCONNECT:
            reason = struct.unpack("<h", message[8:10])[0] if len(message) >= 10 else None
            logger.warning("Dhan feed sent a disconnect notice (code %s)", reason)
            if reason in DHAN_FEED_AUTH_DISCONNECT_CODES:
                self._fail_auth()
            return
        if code in (DHAN_FEED_V2_TICKER, DHAN_FEED_V2_QUOTE, DHAN_FEED_V2_FULL):
            decoded = unpack_v2_packet(message, self._instruments.resolve_from_dhan)
            if decoded is None:
                return
            tick, quote = decoded
            for listener in list(self._tick_listeners):
                with contextlib.suppress(Exception):
                    listener(tick)
            self._dispatch(
                TradeTick(
                    instrument_id=tick.instrument_id,
                    ts=tick.ts,
                    price=tick.price,
                    size=float(tick.last_qty),
                    aggressor_side=AggressorSide.NO_AGGRESSOR,
                    trade_id=f"{tick.ts}-{tick.cum_volume or 0}",
                )
            )
            if quote is not None:
                self._dispatch(quote)
            return

        # Legacy (pre-v2) big-endian layouts, retained for the existing decoders/tests.
        id_resolver = self._instruments.resolve_from_dhan
        event = None
        if code == DHAN_FEED_CODE_LTP:
            event = unpack_ltp_packet(message, id_resolver)
        elif code == DHAN_FEED_CODE_QUOTE:
            event = unpack_quote_packet(message, id_resolver)
        elif code == DHAN_FEED_CODE_DEPTH_20:
            event = unpack_depth_20_packet(message, id_resolver)
        if event is not None:
            self._dispatch(event)
