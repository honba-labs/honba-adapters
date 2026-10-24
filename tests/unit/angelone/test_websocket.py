"""Tests for Angel One SmartStream binary WebSocket decoding."""

from __future__ import annotations

import struct

from honba.adapters.models import MarketDepth
from honba.domain.instrument import InstrumentId
from honba.domain.tick import QuoteTick

from honba_adapters.angelone.instruments import AngelOneInstrumentMaster
from honba_adapters.angelone.websocket import (
    DEPTH_RECORD_FORMAT,
    LTP_STRUCT_FORMAT,
    QUOTE_STRUCT_FORMAT,
    SNAP_QUOTE_HEADER_FORMAT,
    decode_smartstream_packet,
)

RELIANCE = InstrumentId("RELIANCE", "NSE")


class TestAngelOneWebSocket:
    def test_decode_mode1_ltp_packet(self) -> None:
        master = AngelOneInstrumentMaster()
        # Format: <BB25sqqq
        # sub_mode=1, exchange=1 (NSE), token=b"2885\x00...", seq=100, ts=1700000000000, ltp=245050 (2450.50 in paise)
        token_bytes = b"2885".ljust(25, b"\x00")
        packet = struct.pack(
            LTP_STRUCT_FORMAT,
            1,  # mode
            1,  # NSE
            token_bytes,
            100,  # sequence
            1700000000000,  # ts ms
            245050,  # ltp (2450.50 paise)
        )
        assert len(packet) == 51

        tick = decode_smartstream_packet(packet, master)
        assert isinstance(tick, QuoteTick)
        assert tick.instrument_id == RELIANCE
        assert tick.bid_price == 2450.50
        assert tick.ask_price == 2450.50

    def test_decode_mode2_quote_packet(self) -> None:
        master = AngelOneInstrumentMaster()
        # Format: <BB25sqqqqqddqqqq
        token_bytes = b"2885".ljust(25, b"\x00")
        packet = struct.pack(
            QUOTE_STRUCT_FORMAT,
            2,  # mode
            1,  # NSE
            token_bytes,
            101,  # sequence
            1700000000000,  # ts ms
            245050,  # ltp
            10,  # last traded qty
            244000,  # atp
            100000,  # volume
            5000.0,  # total buy qty
            4000.0,  # total sell qty
            240000,  # open
            246000,  # high
            239000,  # low
            241000,  # close
        )
        assert len(packet) == 123

        tick = decode_smartstream_packet(packet, master)
        assert isinstance(tick, QuoteTick)
        assert tick.instrument_id == RELIANCE
        assert tick.bid_price == 2450.50
        assert tick.bid_size == 5000.0
        assert tick.ask_size == 4000.0

    def test_decode_mode3_snap_quote_packet(self) -> None:
        master = AngelOneInstrumentMaster()
        token_bytes = b"2885".ljust(25, b"\x00")

        # Header 147 bytes
        header = struct.pack(
            SNAP_QUOTE_HEADER_FORMAT,
            3,  # mode
            1,  # NSE
            token_bytes,
            102,  # sequence
            1700000000000,  # ts ms
            245050,  # ltp
            10,  # last traded qty
            244000,  # atp
            100000,  # volume
            5000.0,  # buy qty
            4000.0,  # sell qty
            240000,  # open
            246000,  # high
            239000,  # low
            241000,  # close
            1700000000000,  # last traded ts
            50000,  # open interest
            10,  # oi change pct
        )
        assert len(header) == 147

        # 10 depth records: 5 buy (flag=0), 5 sell (flag=1)
        depth_records = b""
        for i in range(5):
            depth_records += struct.pack(
                DEPTH_RECORD_FORMAT,
                0,  # buy
                100 + i * 10,
                245000 - i * 50,  # price
                1,
            )
        for i in range(5):
            depth_records += struct.pack(
                DEPTH_RECORD_FORMAT,
                1,  # sell
                150 + i * 10,
                245100 + i * 50,  # price
                1,
            )
        assert len(depth_records) == 200

        # Trailer 32 bytes (upper, lower, high_52, low_52)
        trailer = struct.pack("<qqqq", 260000, 220000, 280000, 200000)
        assert len(trailer) == 32

        full_packet = header + depth_records + trailer
        assert len(full_packet) == 379

        depth = decode_smartstream_packet(full_packet, master)
        assert isinstance(depth, MarketDepth)
        assert depth.instrument_id == RELIANCE
        assert len(depth.bids) == 5
        assert len(depth.asks) == 5
        assert depth.bids[0].price == 2450.00
        assert depth.asks[0].price == 2451.00
