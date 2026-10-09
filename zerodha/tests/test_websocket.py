"""Tests for Zerodha KiteTicker binary WebSocket packet decoding (M3)."""

from __future__ import annotations

import struct

from honba.domain.instrument import InstrumentId
from honba.domain.tick import QuoteTick

from honba_zerodha.websocket import (
    unpack_full_depth_packet,
    unpack_kite_packet,
    unpack_ltp_packet,
    unpack_quote_packet,
)


def mock_id_resolver(token: int) -> InstrumentId | None:
    if token == 738561:
        return InstrumentId("RELIANCE", "NSE")
    return InstrumentId(str(token), "NSE")


def test_unpack_ltp_packet() -> None:
    # 8 bytes: token (4B uint32), ltp_in_paise (4B uint32)
    data = struct.pack(">II", 738561, 245050)
    tick = unpack_ltp_packet(data, mock_id_resolver)
    assert tick is not None
    assert tick.instrument_id == InstrumentId("RELIANCE", "NSE")
    assert tick.bid_price == 2450.5
    assert tick.ask_price == 2450.5


def test_unpack_quote_packet() -> None:
    # 44 bytes: token (4B), ltp (4B int), last_qty (4B), avg_price (4B), volume (4B),
    # buy_qty (4B), sell_qty (4B), open (4B), high (4B), low (4B), close (4B)
    data = struct.pack(
        ">IiIiIIIiiii",
        738561,
        245500,  # 2455.0
        10,
        245000,
        15000,
        1000,
        2000,
        244000,
        246000,
        243500,
        245000,
    )
    quote = unpack_quote_packet(data, mock_id_resolver)
    assert quote is not None
    assert quote.instrument_id == InstrumentId("RELIANCE", "NSE")
    assert quote.bid_price == 2455.0
    assert quote.bid_size == 1000.0
    assert quote.ask_size == 2000.0


def test_unpack_full_depth_packet() -> None:
    # 184 bytes:
    # 44 bytes quote + 20 bytes timestamps/oi + 60 bytes bids (5*12) + 60 bytes asks (5*12)
    quote_part = struct.pack(
        ">IiIiIIIiiii",
        738561,
        245500,
        10,
        245000,
        15000,
        1000,
        2000,
        244000,
        246000,
        243500,
        245000,
    )
    extra_part = struct.pack(">IIIII", 1700000000, 0, 0, 0, 1700000000)

    # 5 bids: qty (4B), price (4B in paise), orders (2B), padding (2B)
    bids_part = bytearray()
    for i in range(5):
        bids_part.extend(struct.pack(">IiHH", 100 * (i + 1), (2454 - i) * 100, 5, 0))

    # 5 asks: qty (4B), price (4B in paise), orders (2B), padding (2B)
    asks_part = bytearray()
    for i in range(5):
        asks_part.extend(struct.pack(">IiHH", 150 * (i + 1), (2456 + i) * 100, 3, 0))

    full_payload = quote_part + extra_part + bytes(bids_part) + bytes(asks_part)
    assert len(full_payload) == 184

    depth = unpack_full_depth_packet(full_payload, mock_id_resolver)
    assert depth is not None
    assert depth.instrument_id == InstrumentId("RELIANCE", "NSE")
    assert len(depth.bids) == 5
    assert len(depth.asks) == 5
    assert depth.bids[0].price == 2454.0
    assert depth.asks[0].price == 2456.0


def test_unpack_composite_frame() -> None:
    # Composite frame: 2 bytes packet count (2), then for each packet: 2 bytes len + payload
    pkt1 = struct.pack(">II", 738561, 245000)
    pkt2 = struct.pack(">II", 738561, 245100)

    frame = bytearray()
    frame.extend(struct.pack(">H", 2))  # num_packets = 2
    frame.extend(struct.pack(">H", len(pkt1)))
    frame.extend(pkt1)
    frame.extend(struct.pack(">H", len(pkt2)))
    frame.extend(pkt2)

    events = unpack_kite_packet(bytes(frame), mock_id_resolver)
    assert len(events) == 2
    assert isinstance(events[0], QuoteTick) and events[0].bid_price == 2450.0
    assert isinstance(events[1], QuoteTick) and events[1].bid_price == 2451.0
