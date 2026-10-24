"""Unit tests for the pure KiteTicker frame decoder, trade filter and tick conversion.

The frames and expected values are golden vectors copied from the Rust crate
``honba-broker-zerodha`` (``src/tests/ticker.rs``) so both languages are checked against
identical data.
"""

from __future__ import annotations

import struct

import pytest
from honba.domain.instrument import InstrumentId
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick

from honba_adapters.zerodha.websocket import (
    DecodedTick,
    Mode,
    Segment,
    TickerError,
    TradeFilter,
    UnknownPacketLengthError,
    decode_frame,
    tick_to_events,
    unpack_kite_packet,
)

NSE_TOKEN = 408_065 * 256 + 1
INST = InstrumentId("RELIANCE", "NSE")


def u32s(*values: int) -> bytes:
    return b"".join(struct.pack(">I", v) for v in values)


def frame(packets: list[bytes]) -> bytes:
    out = struct.pack(">H", len(packets))
    for p in packets:
        out += struct.pack(">H", len(p)) + p
    return out


def ltp_packet(token: int, ltp: int) -> bytes:
    return u32s(token, ltp)


def quote_packet(token: int) -> bytes:
    # ltp, last_qty, avg, volume, buy_qty, sell_qty, open, high, low, close
    return u32s(token, 250_050, 10, 250_000, 5000, 100, 200, 249_000, 251_000, 248_000, 249_500)


def full_packet(token: int, bid: int, ask: int) -> bytes:
    p = quote_packet(token) + u32s(1_700_000_000, 7, 8, 6, 1_700_000_100)
    for i in range(10):
        price = bid if i < 5 else ask
        p += u32s(10 + i, price) + struct.pack(">H", i + 1) + b"\x00\x00"
    assert len(p) == 184
    return p


def tick_of(packet: bytes) -> DecodedTick:
    return decode_frame(frame([packet]))[0]


class TestDecodeFrame:
    def test_heartbeat_decodes_to_nothing(self) -> None:
        assert decode_frame(b"\x00") == []

    def test_ltp_packet_decodes(self) -> None:
        t = decode_frame(frame([ltp_packet(NSE_TOKEN, 250_075)]))
        assert len(t) == 1
        assert t[0].token == NSE_TOKEN
        assert t[0].segment is Segment.NSE
        assert t[0].mode is Mode.LTP
        assert t[0].ltp == pytest.approx(2500.75)
        assert t[0].volume is None

    def test_cds_and_bcd_use_wider_divisors(self) -> None:
        cds = decode_frame(frame([ltp_packet(256 * 5 + 3, 835_000_000)]))
        assert cds[0].segment is Segment.CDS
        assert cds[0].ltp == pytest.approx(83.5)
        bcd = decode_frame(frame([ltp_packet(256 * 5 + 6, 835_000)]))
        assert bcd[0].segment is Segment.BCD
        assert bcd[0].ltp == pytest.approx(83.5)

    def test_segment_codes(self) -> None:
        expected = {
            1: Segment.NSE,
            2: Segment.NFO,
            3: Segment.CDS,
            4: Segment.BSE,
            5: Segment.BFO,
            6: Segment.BCD,
            7: Segment.MCX,
            8: Segment.MCX_SX,
            9: Segment.INDICES,
        }
        for code, seg in expected.items():
            assert decode_frame(frame([ltp_packet(256 + code, 100)]))[0].segment is seg
        assert decode_frame(frame([ltp_packet(256 + 77, 100)]))[0].segment is Segment.UNKNOWN

    def test_quote_packet_decodes(self) -> None:
        t = tick_of(quote_packet(NSE_TOKEN))
        assert t.mode is Mode.QUOTE
        assert t.last_qty == 10
        assert t.volume == 5000
        assert t.open == 2490.0
        assert t.high == 2510.0
        assert t.low == 2480.0
        assert t.close == 2495.0
        assert (t.buy_qty, t.sell_qty) == (100, 200)
        assert t.bids == () and t.asks == ()
        assert t.exchange_ts_secs is None

    def test_index_packets_decode(self) -> None:
        token = 256 * 9 + 9
        # token, ltp, high, low, open, close, change
        p = u32s(token, 2_200_000, 2_210_000, 2_190_000, 2_195_000, 2_180_000, 5)
        t = tick_of(p)
        assert t.segment is Segment.INDICES
        assert t.mode is Mode.QUOTE
        assert t.high == 22_100.0
        assert t.low == 21_900.0
        assert t.open == 21_950.0
        assert t.close == 21_800.0
        assert t.exchange_ts_secs is None
        t = tick_of(p + u32s(1_700_000_000))
        assert t.mode is Mode.FULL
        assert t.exchange_ts_secs == 1_700_000_000

    def test_full_packet_decodes_depth(self) -> None:
        t = tick_of(full_packet(NSE_TOKEN, 250_000, 250_100))
        assert t.mode is Mode.FULL
        assert t.exchange_ts_secs == 1_700_000_100
        assert len(t.bids) == 5 and len(t.asks) == 5
        assert t.bids[0].qty == 10
        assert t.bids[0].orders == 1
        assert t.bids[0].price == pytest.approx(2500.0)
        assert t.asks[0].price == pytest.approx(2501.0)
        assert t.asks[4].orders == 10

    def test_multi_packet_frame_decodes_in_order(self) -> None:
        f = frame([ltp_packet(NSE_TOKEN, 100), ltp_packet(NSE_TOKEN + 256, 200)])
        t = decode_frame(f)
        assert len(t) == 2
        assert t[1].token == NSE_TOKEN + 256

    def test_malformed_frames_raise_without_other_exceptions(self) -> None:
        good = frame([ltp_packet(NSE_TOKEN, 100)])
        for cut in range(2, len(good)):
            with pytest.raises(TickerError):
                decode_frame(good[:cut])
        with pytest.raises(TickerError):
            decode_frame(b"")

    def test_unknown_length_is_reported(self) -> None:
        with pytest.raises(UnknownPacketLengthError) as exc:
            decode_frame(frame([bytes(12)]))
        assert exc.value.length == 12

    def test_arbitrary_bytes_never_raise_anything_but_ticker_error(self) -> None:
        import random

        rng = random.Random(7)
        for _ in range(300):
            blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
            try:
                decode_frame(blob)
            except TickerError:
                pass


class TestToEvents:
    def test_ltp_maps_to_trade_with_ts_init(self) -> None:
        t = tick_of(ltp_packet(NSE_TOKEN, 250_075))
        ev = tick_to_events(t, INST, 42)
        assert len(ev) == 1
        tr = ev[0]
        assert isinstance(tr, TradeTick)
        assert tr.price == 2500.75
        assert tr.size == 0.0
        assert tr.aggressor_side is AggressorSide.NO_AGGRESSOR
        assert tr.ts == 42
        assert tr.trade_id == f"{NSE_TOKEN}-42-0"

    def test_quote_maps_to_sized_trade(self) -> None:
        ev = tick_to_events(tick_of(quote_packet(NSE_TOKEN)), INST, 42)
        assert len(ev) == 1
        assert isinstance(ev[0], TradeTick)
        assert ev[0].size == 10.0

    def test_full_maps_to_trade_and_quote_with_exchange_ts(self) -> None:
        ev = tick_to_events(tick_of(full_packet(NSE_TOKEN, 250_000, 250_100)), INST, 1)
        assert len(ev) == 2
        expected_ts = 1_700_000_100 * 1_000_000_000
        tr, q = ev
        assert isinstance(tr, TradeTick) and isinstance(q, QuoteTick)
        assert tr.ts == expected_ts
        assert (q.bid_price, q.ask_price) == (2500.0, 2501.0)
        assert (q.bid_size, q.ask_size) == (10.0, 15.0)
        assert q.ts == expected_ts

    def test_full_skips_quote_when_crossed_or_empty(self) -> None:
        crossed = tick_of(full_packet(NSE_TOKEN, 250_200, 250_100))
        assert len(tick_to_events(crossed, INST, 1)) == 1
        empty = tick_of(full_packet(NSE_TOKEN, 0, 250_100))
        assert len(tick_to_events(empty, INST, 1)) == 1


class TestTradeFilter:
    def test_full_baseline_emits_quote_but_no_trade(self) -> None:
        f = TradeFilter()
        t = tick_of(full_packet(NSE_TOKEN, 250_000, 250_100))
        m = f.events(t, INST, 1)
        assert len(m) == 1 and isinstance(m[0], QuoteTick)
        # Same volume again: still a quote (depth may have changed), still no trade.
        m = f.events(t, INST, 1)
        assert len(m) == 1 and isinstance(m[0], QuoteTick)

    def test_volume_decrease_resets_baseline_without_trade(self) -> None:
        f = TradeFilter()
        base = tick_of(quote_packet(NSE_TOKEN))

        def with_volume(v: int) -> DecodedTick:
            from dataclasses import replace

            return replace(base, volume=v)

        assert f.events(with_volume(500), INST, 1) == []
        assert f.events(with_volume(100), INST, 1) == []
        m = f.events(with_volume(130), INST, 1)
        assert isinstance(m[0], TradeTick)
        assert m[0].size == 30.0
        assert m[0].trade_id.endswith("-130")

    def test_ltp_only_trades_only_when_price_changes_with_unique_ids(self) -> None:
        f = TradeFilter()
        seq = [100, 100, 200, 100]
        out: list[TradeTick] = []
        for ltp in seq:
            for e in f.events(tick_of(ltp_packet(NSE_TOKEN, ltp)), INST, 5):
                assert isinstance(e, TradeTick)
                out.append(e)
        assert [(e.price, e.size) for e in out] == [(2.0, 0.0), (1.0, 0.0)]
        assert out[0].trade_id != out[1].trade_id

    def test_volume_packets_emit_trade_only_when_volume_increases(self) -> None:
        def quote44(ltp: int, last_qty: int, volume: int) -> bytes:
            return u32s(NSE_TOKEN, ltp, last_qty, 0, volume, 0, 0, 0, 0, 0, 0)

        f = TradeFilter()
        packets = [
            quote44(100, 7, 1000),
            quote44(100, 7, 1000),
            quote44(101, 3, 1005),
            quote44(102, 4, 1012),
        ]
        trades: list[TradeTick] = []
        for p in packets:
            trades += [e for e in f.events(tick_of(p), INST, 5) if isinstance(e, TradeTick)]
        assert [(t.price, t.size) for t in trades] == [(1.01, 5.0), (1.02, 7.0)]
        assert trades[0].trade_id != trades[1].trade_id
        assert trades[0].trade_id.endswith("-1005") and trades[1].trade_id.endswith("-1012")

    def test_baseline_is_per_token(self) -> None:
        f = TradeFilter()
        other = NSE_TOKEN + 256
        assert f.events(tick_of(ltp_packet(NSE_TOKEN, 100)), INST, 1) == []
        assert f.events(tick_of(ltp_packet(other, 100)), INST, 1) == []
        m = f.events(tick_of(ltp_packet(NSE_TOKEN, 150)), INST, 1)
        assert len(m) == 1 and isinstance(m[0], TradeTick) and m[0].price == 1.5


class TestLegacyUnpackKeepsWorking:
    def test_unpack_kite_packet_handles_index_and_wider_divisor_packets(self) -> None:
        resolver = lambda token: InstrumentId(str(token), "NSE")
        cds = unpack_kite_packet(frame([ltp_packet(256 * 5 + 3, 835_000_000)]), resolver)
        assert (
            len(cds) == 1
            and isinstance(cds[0], QuoteTick)
            and cds[0].bid_price == pytest.approx(83.5)
        )
        index = u32s(256 * 9 + 9, 2_200_000, 2_210_000, 2_190_000, 2_195_000, 2_180_000, 5)
        ev = unpack_kite_packet(frame([index]), resolver)
        assert len(ev) == 1 and isinstance(ev[0], QuoteTick) and ev[0].bid_price == 22_000.0

    def test_unpack_kite_packet_drops_malformed_frames(self) -> None:
        resolver = lambda token: None
        assert unpack_kite_packet(b"\x00\x05\x00", resolver) == []
