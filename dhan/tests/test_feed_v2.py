"""Feed v2 decoding (little-endian float32) and DhanFeedClient/adapter wiring, fakes only."""

from __future__ import annotations

import json
import struct
from typing import Any

import httpx
import pytest
from honba.adapters.models import StreamMode
from honba.domain.instrument import InstrumentId
from honba.domain.tick import QuoteTick, TradeTick

from honba_dhan import DhanAdapter, DhanConfig
from honba_dhan.instruments import DhanInstrumentMaster
from honba_dhan.websocket import DhanFeedClient, FeedTick, unpack_v2_packet

RELIANCE = InstrumentId("RELIANCE", "NSE")
TCS = InstrumentId("TCS", "NSE")
LTT = 1_704_167_700  # 2024-01-02 03:55:00 UTC
NOW_NS = 5 * 10**18


def _resolver(sec: str, seg: str) -> InstrumentId | None:
    return {"1333": RELIANCE, "11536": TCS}.get(sec)


def _hdr(code: int, length: int, seg: int, sec: int) -> bytes:
    return struct.pack("<BHBI", code, length, seg, sec)


def ticker(ltp: float, ltt: int = LTT, sec: int = 1333) -> bytes:
    return _hdr(2, 16, 1, sec) + struct.pack("<fI", ltp, ltt)


def quote(ltp: float, vol: int, ltt: int = LTT, sec: int = 1333, ltq: int = 7) -> bytes:
    body = struct.pack(
        "<fHIfIIIffff", ltp, ltq, ltt, ltp, vol, 10, 20, 2400.0, 2390.0, 2460.0, 2380.0
    )
    return _hdr(4, 50, 1, sec) + body


def full(ltp: float, vol: int, bid: float, ask: float, ltt: int = LTT) -> bytes:
    head = struct.pack("<fHIfIIIIIIffff", ltp, 3, ltt, ltp, vol, 1, 2, 0, 0, 0, 1.0, 1.0, 1.0, 1.0)
    levels = struct.pack("<IIHHff", 100, 200, 3, 4, bid, ask) + b"\x00" * 80
    return _hdr(8, 162, 1, 1333) + head + levels


class TestDecode:
    def test_ticker_uses_exchange_time_and_float32_price(self) -> None:
        out = unpack_v2_packet(ticker(2450.55), _resolver, now_ns=NOW_NS)
        assert out is not None
        tick, q = out
        assert tick == FeedTick(RELIANCE, LTT * 10**9, 2450.55, None, 0)
        assert q is None

    def test_quote_has_cumulative_volume_and_ltq(self) -> None:
        out = unpack_v2_packet(quote(2451.25, 123456, ltq=9), _resolver, now_ns=NOW_NS)
        assert out is not None
        tick, _ = out
        assert tick.cum_volume == 123456
        assert tick.last_qty == 9
        assert tick.price == 2451.25
        assert tick.ts == LTT * 10**9

    def test_zero_ltt_falls_back_to_receive_time(self) -> None:
        out = unpack_v2_packet(ticker(100.0, ltt=0), _resolver, now_ns=NOW_NS)
        assert out is not None and out[0].ts == NOW_NS

    def test_full_packet_yields_real_top_of_book(self) -> None:
        out = unpack_v2_packet(full(2450.0, 10, 2449.95, 2450.05), _resolver, now_ns=NOW_NS)
        assert out is not None
        tick, q = out
        assert q is not None
        assert (q.bid_price, q.ask_price, q.bid_size, q.ask_size) == (
            2449.95,
            2450.05,
            100.0,
            200.0,
        )
        assert q.ts == tick.ts == LTT * 10**9

    def test_unknown_security_gets_numeric_symbol(self) -> None:
        out = unpack_v2_packet(ticker(10.0, sec=999), _resolver, now_ns=NOW_NS)
        assert out is not None and out[0].instrument_id == InstrumentId("999", "NSE")

    @pytest.mark.parametrize(
        "raw", [b"", b"\x02\x00", ticker(1.0)[:15], quote(1.0, 1)[:49], b"\x07" * 20]
    )
    def test_short_or_unknown_frames_are_ignored(self, raw: bytes) -> None:
        assert unpack_v2_packet(raw, _resolver, now_ns=NOW_NS) is None


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.is_connected = True
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_json(self, payload: Any) -> None:
        self.sent.append(payload)


def _client(ws: FakeWS | None = None) -> tuple[DhanFeedClient, FakeWS]:
    ws = ws or FakeWS()
    cfg = DhanConfig.live(client_id="cid", access_token="tok")
    return DhanFeedClient(cfg, DhanInstrumentMaster(), ws=ws), ws


class TestClient:
    def test_url_carries_v2_auth_query(self) -> None:
        url = DhanFeedClient._build_url(DhanConfig.live(client_id="cid", access_token="tok"))
        assert url.startswith("wss://api-feed.dhan.co?")
        assert "version=2" in url and "token=tok" in url and "clientId=cid" in url
        assert "authType=2" in url

    def test_repr_does_not_leak_credentials(self) -> None:
        c, _ = _client()
        assert "tok" not in repr(c) and "cid" not in repr(c)

    async def test_subscribe_sends_mode_specific_request(self) -> None:
        c, ws = _client()
        await c.subscribe((RELIANCE, TCS), mode=StreamMode.QUOTE)
        assert ws.sent == [
            {
                "RequestCode": 17,
                "InstrumentCount": 2,
                "InstrumentList": [
                    {"ExchangeSegment": "NSE_EQ", "SecurityId": "1333"},
                    {"ExchangeSegment": "NSE_EQ", "SecurityId": "11536"},
                ],
            }
        ]
        await c.unsubscribe((RELIANCE,), callback=None)
        assert ws.sent[-1]["RequestCode"] == 18
        assert ws.sent[-1]["InstrumentList"] == [
            {"ExchangeSegment": "NSE_EQ", "SecurityId": "1333"}
        ]

    async def test_no_auth_message_on_connect_but_subscriptions_restored(self) -> None:
        c, ws = _client()
        ws.is_connected = False
        await c.subscribe((RELIANCE,), mode=StreamMode.LTP)
        assert ws.sent == []
        ws.is_connected = True
        await c._on_connected()
        assert [m["RequestCode"] for m in ws.sent] == [15]
        assert all("token" not in json.dumps(m) for m in ws.sent)

    async def test_callbacks_are_filtered_per_subscription(self) -> None:
        c, _ = _client()
        got_a: list[Any] = []
        got_b: list[Any] = []
        await c.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=got_a.append)
        await c.subscribe((TCS,), mode=StreamMode.QUOTE, callback=got_b.append)
        await c._on_message(quote(2450.0, 100))
        assert len(got_a) == 1 and isinstance(got_a[0], TradeTick)
        assert got_a[0].price == 2450.0 and got_a[0].ts == LTT * 10**9
        assert got_b == []

    async def test_full_packet_emits_trade_and_quote(self) -> None:
        c, _ = _client()
        got: list[Any] = []
        await c.subscribe((RELIANCE,), mode=StreamMode.DEPTH, callback=got.append)
        await c._on_message(full(2450.0, 10, 2449.95, 2450.05))
        assert [type(e) for e in got] == [TradeTick, QuoteTick]

    async def test_tick_listener_receives_feed_ticks(self) -> None:
        c, _ = _client()
        ticks: list[FeedTick] = []
        c.add_tick_listener(ticks.append)
        await c._on_message(quote(2450.0, 555))
        assert ticks[0].cum_volume == 555

    async def test_bad_callback_does_not_break_others(self) -> None:
        c, _ = _client()
        got: list[Any] = []

        def boom(_e: Any) -> None:
            raise RuntimeError("x")

        await c.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=boom)
        await c.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=got.append)
        await c._on_message(ticker(1.0))
        assert len(got) == 1

    async def test_unsubscribe_keeps_wire_sub_while_another_listener_wants_it(self) -> None:
        c, ws = _client()
        a: list[Any] = []
        b: list[Any] = []
        cb_a, cb_b = a.append, b.append
        await c.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=cb_a)
        await c.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=cb_b)
        await c.unsubscribe((RELIANCE,), callback=cb_a)
        assert [m["RequestCode"] for m in ws.sent if m["RequestCode"] == 18] == []
        await c.unsubscribe((RELIANCE,), callback=cb_b)
        assert ws.sent[-1]["RequestCode"] == 18

    async def test_text_and_disconnect_frames_ignored(self) -> None:
        c, _ = _client()
        got: list[Any] = []
        await c.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=got.append)
        await c._on_message("hello")
        await c._on_message(_hdr(50, 10, 1, 1333) + b"\x00\x00")
        assert got == []


class TestAdapterWiring:
    async def _adapter(self, ws: FakeWS) -> DhanAdapter:
        def factory(cfg: DhanConfig, inst: DhanInstrumentMaster) -> DhanFeedClient:
            return DhanFeedClient(cfg, inst, ws=ws)

        return DhanAdapter(
            DhanConfig.live(client_id="c", access_token="t"),
            http_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
            feed_factory=factory,
        )

    async def test_connect_starts_feed_and_disconnect_stops_it(self) -> None:
        ws = FakeWS()
        a = await self._adapter(ws)
        await a.connect()
        assert ws.started and a.feed is not None
        await a.disconnect()
        assert ws.stopped and a.feed is None

    async def test_subscribe_streams_and_unsubscribe_stops(self) -> None:
        ws = FakeWS()
        a = await self._adapter(ws)
        await a.connect()
        got: list[Any] = []
        sub = await a.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=got.append)
        assert ws.sent and ws.sent[0]["RequestCode"] == 17
        assert a.feed is not None
        await a.feed._on_message(quote(2450.0, 10))
        assert len(got) == 1
        await a.unsubscribe(sub.id)
        assert ws.sent[-1]["RequestCode"] == 18
        await a.feed._on_message(quote(2451.0, 11))
        assert len(got) == 1
