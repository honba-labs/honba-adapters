"""Binary WebSocket feed decoder and client for Zerodha KiteTicker.

Connects to ``wss://ws.kite.trade`` and decodes KiteTicker binary frames. The decoder is pure
(no I/O, no clock) and mirrors the protocol exactly:

- A frame is ``[count:u16] ([len:u16] [payload])*``; a single-byte frame is a heartbeat.
- Payload lengths: 8 (LTP), 28 / 32 (index quote / full), 44 (quote), 184 (full with five bid
  and five ask levels). Prices are integers divided by 100, except CDS (10^7) and BCD (10^4),
  selected by the low byte of the instrument token.
- Malformed or truncated frames and undefined packet sizes raise ``TickerError``.

Decoded ticks become canonical Honba events: ``TradeTick`` only when a trade demonstrably
happened (``TradeFilter``), ``QuoteTick`` from a valid best bid/ask, and ``MarketDepth``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from honba.adapters.errors import AdapterError
from honba.adapters.models import DepthLevel, MarketDepth, StreamCallback, StreamMode
from honba.domain.instrument import InstrumentId
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick
from honba_adapters_shared.websocket import ReconnectingWebSocketClient

from honba_zerodha.constants import ZERODHA_STREAM_MODE_MAP

if TYPE_CHECKING:
    from honba_zerodha.config import ZerodhaConfig
    from honba_zerodha.instruments import ZerodhaInstrumentMaster

logger = logging.getLogger("honba.adapters.zerodha.websocket")

__all__ = [
    "MAX_SUBSCRIPTIONS",
    "DecodedDepth",
    "DecodedTick",
    "Mode",
    "Segment",
    "TickerError",
    "TradeFilter",
    "UnknownPacketLengthError",
    "ZerodhaFeedClient",
    "decode_frame",
    "decode_packet",
    "tick_to_events",
    "unpack_full_depth_packet",
    "unpack_kite_packet",
    "unpack_ltp_packet",
    "unpack_quote_packet",
]

#: Kite allows at most this many instruments per websocket connection.
MAX_SUBSCRIPTIONS = 3000


class TickerError(ValueError):
    """A ticker frame is truncated, overruns its buffer or has an undefined packet size."""


class UnknownPacketLengthError(TickerError):
    """A packet had a payload length the protocol does not define."""

    def __init__(self, length: int) -> None:
        super().__init__(f"unknown packet length {length}")
        self.length = length


class Segment(Enum):
    """Exchange segment encoded in the low byte of the instrument token."""

    NSE = 1
    NFO = 2
    CDS = 3
    BSE = 4
    BFO = 5
    BCD = 6
    MCX = 7
    MCX_SX = 8
    INDICES = 9
    UNKNOWN = 0


class Mode(Enum):
    """Subscription mode a packet was produced in."""

    LTP = "ltp"
    QUOTE = "quote"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class DecodedDepth:
    """One market depth level as sent on the wire."""

    qty: int
    price: float
    orders: int


@dataclass(frozen=True, slots=True)
class DecodedTick:
    """A decoded ticker packet; optional fields are absent in the shorter packet modes."""

    token: int
    segment: Segment
    mode: Mode
    ltp: float
    last_qty: int | None = None
    volume: int | None = None
    buy_qty: int | None = None
    sell_qty: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    bids: tuple[DecodedDepth, ...] = ()
    asks: tuple[DecodedDepth, ...] = ()
    exchange_ts_secs: int | None = None


_SEGMENT_BY_CODE = {s.value: s for s in Segment if s is not Segment.UNKNOWN}


def segment_of(token: int) -> Segment:
    return _SEGMENT_BY_CODE.get(token & 0xFF, Segment.UNKNOWN)


def _divisor(segment: Segment) -> float:
    if segment is Segment.CDS:
        return 10_000_000.0
    if segment is Segment.BCD:
        return 10_000.0
    return 100.0


def decode_packet(payload: bytes) -> DecodedTick:
    """Decode one packet payload (without its length header)."""
    size = len(payload)
    if size not in (8, 28, 32, 44, 184):
        raise UnknownPacketLengthError(size)

    def u32(off: int) -> int:
        return int(struct.unpack_from(">I", payload, off)[0])

    token = u32(0)
    segment = segment_of(token)
    div = _divisor(segment)

    def price(off: int) -> float:
        return u32(off) / div

    if size == 8:
        return DecodedTick(token, segment, Mode.LTP, price(4))
    if size in (28, 32):
        return DecodedTick(
            token,
            segment,
            Mode.FULL if size == 32 else Mode.QUOTE,
            price(4),
            high=price(8),
            low=price(12),
            open=price(16),
            close=price(20),
            exchange_ts_secs=u32(28) if size == 32 else None,
        )
    bids: list[DecodedDepth] = []
    asks: list[DecodedDepth] = []
    ts: int | None = None
    if size == 184:
        ts = u32(60)
        for i in range(10):
            off = 64 + i * 12
            level = DecodedDepth(
                u32(off), price(off + 4), int(struct.unpack_from(">H", payload, off + 8)[0])
            )
            (bids if i < 5 else asks).append(level)
    return DecodedTick(
        token,
        segment,
        Mode.FULL if size == 184 else Mode.QUOTE,
        price(4),
        last_qty=u32(8),
        volume=u32(16),
        buy_qty=u32(20),
        sell_qty=u32(24),
        open=price(28),
        high=price(32),
        low=price(36),
        close=price(40),
        bids=tuple(bids),
        asks=tuple(asks),
        exchange_ts_secs=ts,
    )


def decode_frame(frame: bytes) -> list[DecodedTick]:
    """Decode one websocket binary message into ticks.

    A single-byte message is a heartbeat and yields no ticks. Truncated or overrunning frames
    raise ``TickerError``; a packet of undefined size raises ``UnknownPacketLengthError``.
    Never raises anything else on arbitrary input.
    """
    if len(frame) == 1:
        return []
    if len(frame) < 2:
        raise TickerError("malformed frame: missing packet count")
    count = struct.unpack_from(">H", frame, 0)[0]
    pos = 2
    out: list[DecodedTick] = []
    for _ in range(count):
        if pos + 2 > len(frame):
            raise TickerError("malformed frame: truncated packet length")
        length = struct.unpack_from(">H", frame, pos)[0]
        pos += 2
        if pos + length > len(frame):
            raise TickerError("malformed frame: packet overruns frame")
        out.append(decode_packet(frame[pos : pos + length]))
        pos += length
    return out


def _event_ts(tick: DecodedTick, ts_init: int) -> int:
    return ts_init if tick.exchange_ts_secs is None else tick.exchange_ts_secs * 1_000_000_000


def _trade_event(
    tick: DecodedTick, instrument: InstrumentId, size: float, discriminator: int, ts_init: int
) -> TradeTick:
    ts_event = _event_ts(tick, ts_init)
    return TradeTick(
        instrument_id=instrument,
        ts=ts_event,
        price=tick.ltp,
        size=size,
        aggressor_side=AggressorSide.NO_AGGRESSOR,
        trade_id=f"{tick.token}-{ts_event}-{discriminator}",
    )


def _quote_event(tick: DecodedTick, instrument: InstrumentId, ts_init: int) -> QuoteTick | None:
    if tick.mode is not Mode.FULL or not tick.bids or not tick.asks:
        return None
    bid, ask = tick.bids[0], tick.asks[0]
    if not (bid.price > 0.0 and ask.price > 0.0 and bid.price <= ask.price):
        return None
    return QuoteTick(
        instrument_id=instrument,
        ts=_event_ts(tick, ts_init),
        bid_price=bid.price,
        ask_price=ask.price,
        bid_size=float(bid.qty),
        ask_size=float(ask.qty),
    )


def tick_to_events(
    tick: DecodedTick, instrument: InstrumentId, ts_init: int
) -> list[TradeTick | QuoteTick]:
    """Convert a decoded packet into domain events, statelessly.

    Every packet yields a ``TradeTick`` at the last price (size is the last quantity when
    present, else zero), so a repeated packet repeats the trade; live feeds must use
    ``TradeFilter`` instead. Full packets with a valid best bid and ask also yield a
    ``QuoteTick``. ``ts`` is the exchange timestamp when present, else ``ts_init``. The trade id
    is ``{token}-{ts}-{volume}`` (volume ``0`` when the packet has none).
    """
    out: list[TradeTick | QuoteTick] = [
        _trade_event(tick, instrument, float(tick.last_qty or 0), tick.volume or 0, ts_init)
    ]
    quote = _quote_event(tick, instrument, ts_init)
    if quote is not None:
        out.append(quote)
    return out


class TradeFilter:
    """Stateful converter that reports a trade only when one demonstrably happened.

    Kite re-sends the same snapshot whenever anything in the packet changes (for example
    depth), so each packet is not a trade. Per token this tracks the last ``(volume, ltp)``:

    - the first packet is the baseline and emits no trade;
    - with volume: a ``TradeTick`` only when volume strictly increased, sized by the delta (a
      decrease, as after a day roll, only resets the baseline);
    - without volume (LTP-only, indices): a size-0 ``TradeTick`` only when the price changed.

    Trade ids are ``{token}-{ts}-{volume}`` or, without volume, a per-token counter, so they
    are unique per trade. A ``QuoteTick`` is emitted for every Full packet with a valid best
    bid and ask.
    """

    def __init__(self) -> None:
        # token -> [last volume | None, last ltp | None, counter]
        self._state: dict[int, list[Any]] = {}

    def events(
        self, tick: DecodedTick, instrument: InstrumentId, ts_init: int
    ) -> list[TradeTick | QuoteTick]:
        st = self._state.setdefault(tick.token, [None, None, 0])
        prev_volume, prev_ltp = st[0], st[1]
        out: list[TradeTick | QuoteTick] = []
        if not (prev_volume is None and prev_ltp is None):
            if tick.volume is not None and prev_volume is not None:
                if tick.volume > prev_volume:
                    out.append(
                        _trade_event(
                            tick, instrument, float(tick.volume - prev_volume), tick.volume, ts_init
                        )
                    )
            elif tick.volume is None and prev_ltp != tick.ltp:
                st[2] += 1
                out.append(_trade_event(tick, instrument, 0.0, st[2], ts_init))
        if tick.volume is not None:
            st[0] = tick.volume
        st[1] = tick.ltp
        quote = _quote_event(tick, instrument, ts_init)
        if quote is not None:
            out.append(quote)
        return out


def _legacy_event(
    tick: DecodedTick, instrument: InstrumentId, ts: int
) -> QuoteTick | MarketDepth | None:
    """The packet as the adapter has always published it: a top-of-book quote or a book.

    Returns ``None`` for a book that violates the ``MarketDepth`` ordering invariant (the
    exchange sent an unsorted or crossed book); the trade and quote events still flow.
    """
    if tick.bids or tick.asks:
        try:
            return MarketDepth(
                instrument_id=instrument,
                ts=ts,
                bids=tuple(
                    DepthLevel(price=lvl.price, quantity=float(lvl.qty))
                    for lvl in tick.bids
                    if lvl.price > 0
                ),
                asks=tuple(
                    DepthLevel(price=lvl.price, quantity=float(lvl.qty))
                    for lvl in tick.asks
                    if lvl.price > 0
                ),
            )
        except ValueError:
            logger.debug("KiteTicker book for token %d violates ordering; skipped", tick.token)
            return None
    has_sizes = tick.buy_qty is not None and tick.sell_qty is not None
    return QuoteTick(
        instrument_id=instrument,
        ts=ts,
        bid_price=tick.ltp,
        ask_price=tick.ltp,
        bid_size=float(tick.buy_qty) if has_sizes and tick.buy_qty is not None else 1.0,
        ask_size=float(tick.sell_qty) if has_sizes and tick.sell_qty is not None else 1.0,
    )


def _resolve_or_token(
    id_resolver: Callable[[int], InstrumentId | None], token: int
) -> InstrumentId:
    return id_resolver(token) or InstrumentId(str(token), "NSE")


def unpack_ltp_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
    ts: int | None = None,
) -> QuoteTick | None:
    """Unpack KiteTicker 8-byte LTP packet."""
    if len(data) < 8:
        return None
    tick = decode_packet(data[:8])
    ev = _legacy_event(tick, _resolve_or_token(id_resolver, tick.token), ts or time.time_ns())
    return ev if isinstance(ev, QuoteTick) else None


def unpack_quote_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
    ts: int | None = None,
) -> QuoteTick | None:
    """Unpack KiteTicker 44-byte Quote packet."""
    if len(data) < 44:
        return None
    tick = decode_packet(data[:44])
    ev = _legacy_event(tick, _resolve_or_token(id_resolver, tick.token), ts or time.time_ns())
    return ev if isinstance(ev, QuoteTick) else None


def unpack_full_depth_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
    ts: int | None = None,
) -> MarketDepth | None:
    """Unpack KiteTicker 184-byte Full Depth packet."""
    if len(data) < 184:
        return None
    tick = decode_packet(data[:184])
    ev = _legacy_event(tick, _resolve_or_token(id_resolver, tick.token), ts or time.time_ns())
    return ev if isinstance(ev, MarketDepth) else None


def unpack_kite_packet(
    data: bytes,
    id_resolver: Callable[[int], InstrumentId | None],
    ts: int | None = None,
) -> list[QuoteTick | MarketDepth]:
    """Unpack a composite KiteTicker binary frame into top-of-book quotes and books.

    A malformed frame or an undefined packet size yields no events (use ``decode_frame`` to
    see the error).
    """
    try:
        ticks = decode_frame(data)
    except TickerError:
        return []
    now = ts or time.time_ns()
    events = (
        _legacy_event(t, _resolve_or_token(id_resolver, t.token), _event_ts(t, now)) for t in ticks
    )
    return [e for e in events if e is not None]


class ZerodhaFeedClient:
    """Manages the KiteTicker websocket: subscriptions, mode negotiation and packet decoding.

    Subscription state stays consistent with what the server has been told:

    - unknown instruments or exceeding ``MAX_SUBSCRIPTIONS`` fail before anything is sent;
    - if the ``subscribe`` message fails, the new tokens are rolled back so a retry starts clean;
    - if only the ``mode`` message fails, the tokens stay subscribed and the mode stays pending,
      so a retry re-sends just the mode;
    - ``unsubscribe`` sends only tokens that are subscribed and updates state after the send;
    - on (re)connect every subscribed token is re-sent with its own mode.

    Ticks for tokens that are not subscribed are dropped. Malformed frames are logged (without
    payload) and dropped; ``{"type": "error"}`` text frames are kept in ``last_error``.
    """

    def __init__(
        self,
        config: ZerodhaConfig,
        instruments: ZerodhaInstrumentMaster,
        *,
        clock: Callable[[], int] | None = None,
        ws_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._instruments = instruments
        self._clock: Callable[[], int] = clock or time.time_ns
        self._callbacks: list[StreamCallback] = []
        self._subscribed: dict[int, StreamMode] = {}
        self._token_instrument: dict[int, InstrumentId] = {}
        self._pending_mode: dict[int, None] = {}
        self._filter = TradeFilter()
        self.last_error: str | None = None

        ws_url = (
            f"{config.ws_endpoint.rstrip('/')}/?api_key={config.api_key}"
            f"&access_token={config.access_token}"
        )
        factory = ws_factory or self._default_ws_factory
        self._ws = factory(ws_url, on_connected=self._on_connected, on_message=self._on_message)

    @staticmethod
    def _default_ws_factory(url: str, *, on_connected: Any, on_message: Any) -> Any:
        return ReconnectingWebSocketClient(
            url, ping_interval=20.0, on_connected=on_connected, on_message=on_message
        )

    @property
    def is_connected(self) -> bool:
        return bool(self._ws.is_connected)

    async def start(self) -> None:
        await self._ws.start()

    async def stop(self) -> None:
        await self._ws.stop()

    def add_callback(self, callback: StreamCallback) -> None:
        self._callbacks.append(callback)

    def remove_callback(self, callback: StreamCallback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _resolve(self, instruments: tuple[InstrumentId, ...]) -> list[tuple[InstrumentId, int]]:
        pairs: list[tuple[InstrumentId, int]] = []
        for inst in instruments:
            token = self._instruments.resolve_token(inst)
            if token is None:
                raise AdapterError(f"unknown instrument {inst}")
            pairs.append((inst, token))
        return pairs

    async def subscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        mode: StreamMode = StreamMode.QUOTE,
        callback: StreamCallback | None = None,
    ) -> None:
        pairs = self._resolve(instruments)
        new: list[tuple[InstrumentId, int]] = []
        remode: list[int] = []
        seen: set[int] = set()
        for inst, token in pairs:
            if token in seen:
                continue
            seen.add(token)
            if token not in self._subscribed:
                new.append((inst, token))
            elif self._subscribed[token] is not mode:
                remode.append(token)
        if len(self._subscribed) + len(new) > MAX_SUBSCRIPTIONS:
            raise AdapterError(
                f"subscription exceeds the {MAX_SUBSCRIPTIONS} instrument limit per connection"
            )
        for token in remode:  # mode change only: just re-send the mode
            self._subscribed[token] = mode
            self._pending_mode[token] = None

        added_callback = callback is not None and callback not in self._callbacks
        if added_callback and callback is not None:
            self.add_callback(callback)
        for inst, token in new:
            self._subscribed[token] = mode
            self._token_instrument[token] = inst
            self._pending_mode[token] = None

        if not self.is_connected:
            return  # sent in full by ``_on_connected``

        if new:
            tokens = [t for _, t in new]
            try:
                await self._ws.send_json({"a": "subscribe", "v": tokens})
            except Exception:
                # Nothing reached the server: roll back so a retry starts clean.
                for t in tokens:
                    self._subscribed.pop(t, None)
                    self._token_instrument.pop(t, None)
                    self._pending_mode.pop(t, None)
                if added_callback and callback is not None:
                    self.remove_callback(callback)
                raise
        await self._flush_modes()

    async def unsubscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        callback: StreamCallback | None = None,
    ) -> None:
        gone: list[int] = []
        for inst in instruments:
            token = self._instruments.resolve_token(inst)
            if token is not None and token in self._subscribed and token not in gone:
                gone.append(token)
        if not gone:
            if callback is not None:
                self.remove_callback(callback)
            return
        if self.is_connected:
            await self._ws.send_json({"a": "unsubscribe", "v": gone})
        if callback is not None:
            self.remove_callback(callback)
        for token in gone:
            self._subscribed.pop(token, None)
            self._token_instrument.pop(token, None)
            self._pending_mode.pop(token, None)

    async def _flush_modes(self) -> None:
        """Send the pending mode messages, one per mode; a failure leaves them pending."""
        by_mode: dict[StreamMode, list[int]] = {}
        for token in self._pending_mode:
            by_mode.setdefault(self._subscribed[token], []).append(token)
        for mode, tokens in by_mode.items():
            name = ZERODHA_STREAM_MODE_MAP.get(mode, "quote")
            await self._ws.send_json({"a": "mode", "v": [name, tokens]})
            for token in tokens:
                self._pending_mode.pop(token, None)

    async def _on_connected(self) -> None:
        """Restore subscriptions upon WebSocket connection establishment."""
        if not self._subscribed:
            return
        await self._ws.send_json({"a": "subscribe", "v": list(self._subscribed)})
        self._pending_mode = dict.fromkeys(self._subscribed)
        await self._flush_modes()

    def _handle_text(self, text: str) -> None:
        try:
            value = json.loads(text)
        except ValueError:
            return
        if isinstance(value, dict) and value.get("type") == "error":
            data = value.get("data")
            self.last_error = data if isinstance(data, str) else "unknown"
            logger.warning("KiteTicker error: %s", self.last_error)

    async def _on_message(self, message: str | bytes) -> None:
        """Handle incoming binary or text frames from KiteTicker."""
        if isinstance(message, str):
            self._handle_text(message)
            return
        try:
            ticks = decode_frame(message)
        except TickerError as e:
            logger.warning("KiteTicker frame dropped: %s", e)
            return
        ts_init = self._clock()
        for tick in ticks:
            instrument = self._token_instrument.get(tick.token)
            if instrument is None or tick.token not in self._subscribed:
                continue
            events: list[Any] = []
            legacy = _legacy_event(tick, instrument, _event_ts(tick, ts_init))
            if legacy is not None:
                events.append(legacy)
            events.extend(self._filter.events(tick, instrument, ts_init))
            for event in events:
                for cb in list(self._callbacks):
                    with contextlib.suppress(Exception):
                        res = cb(event)
                        if hasattr(res, "__await__"):
                            await res
