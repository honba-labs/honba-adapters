"""Tests for Dhan binary WebSocket feed packet decoding (M2)."""

from __future__ import annotations

import struct

from honba.domain.instrument import InstrumentId

from honba_dhan.constants import (
    DHAN_FEED_CODE_DEPTH_20,
    DHAN_FEED_CODE_LTP,
    DHAN_FEED_CODE_QUOTE,
)
from honba_dhan.websocket import (
    unpack_depth_20_packet,
    unpack_ltp_packet,
    unpack_quote_packet,
)


def mock_id_resolver(sec_id: str, seg: str) -> InstrumentId | None:
    if sec_id == "1333":
        return InstrumentId("RELIANCE", "NSE")
    return InstrumentId(sec_id, "NSE")


class TestDhanFeedPackets:
    def test_unpack_ltp_packet(self) -> None:
        header = struct.pack(">BBHI", DHAN_FEED_CODE_LTP, 0, 16, 1333)
        raw = header + struct.pack(">II", 245050, 1700000000)

        tick = unpack_ltp_packet(raw, mock_id_resolver)
        assert tick is not None
        assert tick.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert tick.bid_price == 2450.50
        assert tick.ask_price == 2450.50

    def test_unpack_quote_packet(self) -> None:
        header = struct.pack(">BBHI", DHAN_FEED_CODE_QUOTE, 0, 48, 1333)
        payload = struct.pack(">II", 250000, 100) + b"\x00" * 32
        raw = header + payload

        tick = unpack_quote_packet(raw, mock_id_resolver)
        assert tick is not None
        assert tick.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert tick.bid_price == 2500.00
        assert tick.ask_price == 2500.00

    def test_unpack_depth_20_packet(self) -> None:
        header = struct.pack(">BBHI", DHAN_FEED_CODE_DEPTH_20, 0, 328, 1333)
        levels_payload = bytearray()
        # 20 bids
        for i in range(20):
            p = 250000 - (i * 10)  # descending prices
            q = (i + 1) * 10
            levels_payload.extend(struct.pack(">II", p, q))
        # 20 asks
        for i in range(20):
            p = 250100 + (i * 10)  # ascending prices
            q = (i + 1) * 10
            levels_payload.extend(struct.pack(">II", p, q))

        raw = header + bytes(levels_payload)
        depth = unpack_depth_20_packet(raw, mock_id_resolver)
        assert depth is not None
        assert depth.instrument_id == InstrumentId("RELIANCE", "NSE")
        assert len(depth.bids) == 20
        assert len(depth.asks) == 20
        assert depth.best_bid is not None and depth.best_bid.price == 2500.00
        assert depth.best_ask is not None and depth.best_ask.price == 2501.00
        assert depth.mid_price == 2500.50
