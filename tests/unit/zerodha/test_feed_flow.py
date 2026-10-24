"""KiteTicker feed behaviour ported from honba-broker-zerodha ``feed.rs`` golden tests.

Unit level: ``ZerodhaFeedClient`` against a scripted in-memory websocket (no network).
Integration level: the same client wired through ``ZerodhaAdapter`` and the mock HTTP transport.
"""

from __future__ import annotations

import struct
from collections import deque
from collections.abc import Callable
from typing import Any

import pytest
from honba.adapters.errors import AdapterError
from honba.adapters.models import MarketDepth, Product, StreamMode
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent
from honba.domain.tick import QuoteTick, TradeTick

from honba_adapters.common.errors import WebSocketError
from honba_adapters.zerodha import ZerodhaAdapter, ZerodhaConfig, make_zerodha_mock_transport
from honba_adapters.zerodha.instruments import ZerodhaInstrumentMaster, ZerodhaInstrumentRecord
from honba_adapters.zerodha.websocket import MAX_SUBSCRIPTIONS, ZerodhaFeedClient

REL = 408_065 * 256 + 1
TCS = 2_953_217


def u32s(*values: int) -> bytes:
    return b"".join(struct.pack(">I", v) for v in values)


def frame(packets: list[bytes]) -> bytes:
    out = struct.pack(">H", len(packets))
    for p in packets:
        out += struct.pack(">H", len(p)) + p
    return out


def ltp(token: int, price: int) -> bytes:
    return u32s(token, price)


def inst(sym: str) -> InstrumentId:
    return InstrumentId(sym, "NSE")


class FakeWs:
    """Records sent JSON; scripted send failures; connectedness is switchable."""

    def __init__(self, on_connected: Any, on_message: Any) -> None:
        self.on_connected = on_connected
        self.on_message = on_message
        self.is_connected = True
        self.sent: list[Any] = []
        self.fail: deque[bool] = deque()
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_json(self, payload: Any) -> None:
        if self.fail and self.fail.popleft():
            raise WebSocketError("send failed")
        self.sent.append(payload)


def master() -> ZerodhaInstrumentMaster:
    m = ZerodhaInstrumentMaster()
    m.register(ZerodhaInstrumentRecord(REL, "1", "RELIANCE", "RELIANCE", "NSE", "NSE"))
    m.register(ZerodhaInstrumentRecord(TCS, "2", "TCS", "TCS", "NSE", "NSE"))
    return m


def make_feed(
    m: ZerodhaInstrumentMaster | None = None, clock: Callable[[], int] | None = None
) -> tuple[ZerodhaFeedClient, FakeWs, list[Any]]:
    holder: list[FakeWs] = []

    def factory(url: str, *, on_connected: Any, on_message: Any) -> FakeWs:
        assert url.startswith("wss://ws.kite.trade") and "api_key=k" in url
        assert "access_token=t" in url
        ws = FakeWs(on_connected, on_message)
        holder.append(ws)
        return ws

    cfg = ZerodhaConfig.live(api_key="k", access_token="t")
    feed = ZerodhaFeedClient(cfg, m or master(), clock=clock or (lambda: 5), ws_factory=factory)
    events: list[Any] = []
    feed.add_callback(events.append)
    return feed, holder[0], events


class TestSubscribeProtocol:
    async def test_subscribe_sends_subscribe_then_mode(self) -> None:
        feed, ws, _ = make_feed()
        await feed.subscribe((inst("RELIANCE"), inst("TCS")), mode=StreamMode.QUOTE)
        assert ws.sent == [
            {"a": "subscribe", "v": [REL, TCS]},
            {"a": "mode", "v": ["quote", [REL, TCS]]},
        ]

    @pytest.mark.parametrize(
        ("mode", "name"),
        [(StreamMode.LTP, "ltp"), (StreamMode.DEPTH, "full"), (StreamMode.QUOTE, "quote")],
    )
    async def test_mode_names(self, mode: StreamMode, name: str) -> None:
        feed, ws, _ = make_feed()
        await feed.subscribe((inst("TCS"),), mode=mode)
        assert ws.sent[1] == {"a": "mode", "v": [name, [TCS]]}

    async def test_unknown_symbol_sends_nothing_and_changes_nothing(self) -> None:
        feed, ws, _ = make_feed()
        with pytest.raises(AdapterError, match="NOPE"):
            await feed.subscribe((inst("TCS"), inst("NOPE")), mode=StreamMode.LTP)
        assert ws.sent == []
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        assert ws.sent[0] == {"a": "subscribe", "v": [TCS]}

    async def test_resubscribe_sends_only_new_tokens(self) -> None:
        feed, ws, _ = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        ws.sent.clear()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        assert ws.sent == []
        await feed.subscribe((inst("TCS"), inst("RELIANCE"), inst("RELIANCE")), mode=StreamMode.LTP)
        assert ws.sent[0] == {"a": "subscribe", "v": [REL]}

    async def test_subscribe_over_cap_is_rejected_before_sending(self) -> None:
        m = ZerodhaInstrumentMaster()
        syms = []
        for i in range(MAX_SUBSCRIPTIONS + 1):
            m.register(ZerodhaInstrumentRecord(10_000 + i, str(i), f"S{i}", "S", "NSE", "NSE"))
            syms.append(inst(f"S{i}"))
        feed, ws, _ = make_feed(m)
        await feed.subscribe(tuple(syms[:MAX_SUBSCRIPTIONS]), mode=StreamMode.LTP)
        before = len(ws.sent)
        with pytest.raises(AdapterError):
            await feed.subscribe((syms[MAX_SUBSCRIPTIONS],), mode=StreamMode.LTP)
        assert len(ws.sent) == before

    async def test_unsubscribe_sends_only_subscribed_tokens(self) -> None:
        feed, ws, _ = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        ws.sent.clear()
        await feed.unsubscribe((inst("TCS"), inst("RELIANCE"), inst("NOPE")))
        assert ws.sent == [{"a": "unsubscribe", "v": [TCS]}]
        await feed.unsubscribe((inst("TCS"),))
        assert len(ws.sent) == 1

    async def test_subscribe_send_failure_leaves_nothing_subscribed_and_retry_resends(
        self,
    ) -> None:
        feed, ws, _ = make_feed()
        ws.fail.extend([True])
        with pytest.raises(WebSocketError):
            await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        assert ws.sent == []
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        assert ws.sent == [
            {"a": "subscribe", "v": [TCS]},
            {"a": "mode", "v": ["ltp", [TCS]]},
        ]

    async def test_mode_send_failure_keeps_subscription_and_retry_resends_only_mode(
        self,
    ) -> None:
        feed, ws, events = make_feed()
        ws.fail.extend([False, True])
        with pytest.raises(WebSocketError):
            await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        assert ws.sent == [{"a": "subscribe", "v": [TCS]}]
        ws.sent.clear()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        assert ws.sent == [{"a": "mode", "v": ["ltp", [TCS]]}]
        ws.sent.clear()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        assert ws.sent == []
        # The token counts as subscribed, so ticks flow.
        await ws.on_message(frame([ltp(TCS, 100)]))
        assert events

    async def test_reconnect_restores_each_token_with_its_own_mode(self) -> None:
        feed, ws, _ = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        await feed.subscribe((inst("RELIANCE"),), mode=StreamMode.DEPTH)
        ws.sent.clear()
        await ws.on_connected()
        assert {"a": "subscribe", "v": [TCS, REL]} in ws.sent
        modes = {m["v"][0]: m["v"][1] for m in ws.sent if m["a"] == "mode"}
        assert modes == {"ltp": [TCS], "full": [REL]}

    async def test_subscribe_while_disconnected_defers_until_connected(self) -> None:
        feed, ws, _ = make_feed()
        ws.is_connected = False
        await feed.subscribe((inst("TCS"),), mode=StreamMode.QUOTE)
        assert ws.sent == []
        ws.is_connected = True
        await ws.on_connected()
        assert ws.sent == [
            {"a": "subscribe", "v": [TCS]},
            {"a": "mode", "v": ["quote", [TCS]]},
        ]


class TestMessages:
    async def test_decodes_and_drops_unsubscribed_ticks(self) -> None:
        feed, ws, events = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        await ws.on_message(frame([ltp(TCS, 400_000), ltp(REL, 250_000)]))
        await ws.on_message(frame([ltp(TCS, 410_000), ltp(REL, 251_000)]))
        assert {e.instrument_id.symbol for e in events} == {"TCS"}
        quotes = [e for e in events if isinstance(e, QuoteTick)]
        trades = [e for e in events if isinstance(e, TradeTick)]
        assert [q.bid_price for q in quotes] == [4000.0, 4100.0]
        assert len(trades) == 1 and trades[0].price == 4100.0
        assert all(e.ts == 5 for e in events)

    async def test_skips_heartbeats_and_ignored_text(self) -> None:
        feed, ws, events = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        await ws.on_message(b"\x00")
        await ws.on_message('{"type":"order","data":{}}')
        await ws.on_message("not json")
        assert events == [] and feed.last_error is None

    async def test_text_error_is_recorded(self) -> None:
        feed, ws, _ = make_feed()
        await ws.on_message('{"type":"error","data":"token expired"}')
        assert feed.last_error == "token expired"

    async def test_malformed_frame_is_dropped_without_leaking_payload(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        feed, ws, events = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.LTP)
        with caplog.at_level("DEBUG"):
            await ws.on_message(bytes([0, 5, 0xAB, 0xCD]))
        assert events == []
        assert "abcd" not in caplog.text.lower() and "171" not in caplog.text

    async def test_depth_subscription_emits_depth_and_valid_best_quote(self) -> None:
        feed, ws, events = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.DEPTH)
        p = u32s(TCS, 250_050, 10, 250_000, 5000, 100, 200, 249_000, 251_000, 248_000, 249_500)
        p += u32s(1_700_000_000, 7, 8, 6, 1_700_000_100)
        for i in range(10):
            p += u32s(10 + i, 250_000 if i < 5 else 250_100) + struct.pack(">H", i + 1) + b"\0\0"
        await ws.on_message(frame([p]))
        # Golden Rust vector has flat levels, which MarketDepth refuses: no book, but the
        # trade/quote events still flow.
        assert not any(isinstance(e, MarketDepth) for e in events)
        q = [e for e in events if isinstance(e, QuoteTick)]
        assert len(q) == 1 and (q[0].bid_price, q[0].ask_price) == (2500.0, 2501.0)
        assert q[0].ts == 1_700_000_100 * 1_000_000_000

    async def test_ordered_book_is_published_as_market_depth(self) -> None:
        feed, ws, events = make_feed()
        await feed.subscribe((inst("TCS"),), mode=StreamMode.DEPTH)
        p = u32s(TCS, 250_050, 10, 250_000, 5000, 100, 200, 249_000, 251_000, 248_000, 249_500)
        p += u32s(1_700_000_000, 7, 8, 6, 1_700_000_100)
        for i in range(10):
            price = 250_000 - i * 10 if i < 5 else 250_100 + (i - 5) * 10
            p += u32s(10 + i, price) + struct.pack(">H", i + 1) + b"\0\0"
        await ws.on_message(frame([p]))
        depth = [e for e in events if isinstance(e, MarketDepth)]
        assert len(depth) == 1
        assert depth[0].bids[0].price == 2500.0 and depth[0].asks[0].price == 2501.0
        assert len(depth[0].bids) == 5 and len(depth[0].asks) == 5


class TestAdapterIntegration:
    async def _adapter(self) -> tuple[ZerodhaAdapter, FakeWs, list[Any]]:
        m = master()
        feed, ws, events = make_feed(m)
        adapter = ZerodhaAdapter(
            ZerodhaConfig.live(api_key="k", access_token="t"),
            http_transport=make_zerodha_mock_transport(),
            instruments=m,
            feed=feed,
        )
        await adapter.connect()
        return adapter, ws, events

    async def test_subscribe_streams_events_to_callback_and_unsubscribe_stops(self) -> None:
        adapter, ws, _ = await self._adapter()
        got: list[Any] = []
        sub = await adapter.subscribe((inst("TCS"),), mode=StreamMode.LTP, callback=got.append)
        assert {"a": "subscribe", "v": [TCS]} in ws.sent
        await ws.on_message(frame([ltp(TCS, 100)]))
        await ws.on_message(frame([ltp(TCS, 200)]))
        assert any(isinstance(e, TradeTick) for e in got)
        await adapter.unsubscribe(sub.id)
        assert ws.sent[-1] == {"a": "unsubscribe", "v": [TCS]}
        n = len(got)
        await ws.on_message(frame([ltp(TCS, 300)]))
        assert len(got) == n

    async def test_failed_subscribe_leaves_no_stale_subscription(self) -> None:
        adapter, ws, _ = await self._adapter()
        ws.fail.extend([True])
        with pytest.raises(WebSocketError):
            await adapter.subscribe((inst("TCS"),), mode=StreamMode.LTP, callback=lambda e: None)
        assert adapter._subscriptions == {}
        sub = await adapter.subscribe((inst("TCS"),), mode=StreamMode.LTP, callback=lambda e: None)
        assert sub.id in adapter._subscriptions

    async def test_adapter_still_orders_with_a_feed_attached(self) -> None:
        adapter, _, _ = await self._adapter()
        report = await adapter.place_order(
            OrderIntent.market_buy(inst("TCS"), 1.0), product=Product.INTRADAY
        )
        assert report.order_id.startswith("KITE-ORD-")
