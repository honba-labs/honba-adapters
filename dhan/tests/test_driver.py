"""DhanBarDriver end to end with a fake websocket, fake HTTP and a fake clock (no network)."""

from __future__ import annotations

import datetime as dt
import struct
from typing import Any

import httpx
import pytest
from honba.domain.instrument import InstrumentId

from honba_dhan import DhanAdapter, DhanConfig
from honba_dhan.cli import build_parser, main
from honba_dhan.driver import MAX_BATCH, DhanBarDriver, IngestFatalError, parse_symbol_spec
from honba_dhan.websocket import DhanFeedClient

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T0 = dt.datetime(2024, 1, 2, 9, 15, tzinfo=IST)


def epoch(minute: float) -> int:
    return int(T0.timestamp() + minute * 60)


def quote_pkt(ltp: float, cum: int, minute: float, sec: int = 1333) -> bytes:
    body = struct.pack("<fHIfIIIffff", ltp, 1, epoch(minute), ltp, cum, 0, 0, 1.0, 1.0, 1.0, 1.0)
    return struct.pack("<BHBI", 4, 50, 1, sec) + body


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.is_connected = True

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def send_json(self, payload: Any) -> None:
        self.sent.append(payload)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> dt.datetime:
        return self.now.astimezone(dt.timezone.utc)

    def at(self, minute: float) -> None:
        self.now = T0 + dt.timedelta(minutes=minute)


class Server:
    def __init__(self, statuses: list[int] | None = None) -> None:
        self.statuses = list(statuses or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, url: str, token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, token, payload))
        status = self.statuses.pop(0) if self.statuses else 200
        body = (
            {"error": {"code": "x", "context": {"reason": "bar_ingest_full"}}}
            if status != 200
            else {}
        )
        return status, body

    @property
    def bars(self) -> list[dict[str, Any]]:
        return [b for _, _, p in self.calls for b in p["bars"]]


def make(server: Server, clock: Clock, symbols: list[str] | None = None, **kw: Any):
    ws = FakeWS()
    adapter = DhanAdapter(
        DhanConfig.live(client_id="c", access_token="t"),
        http_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        feed_factory=lambda cfg, inst: DhanFeedClient(cfg, inst, ws=ws),
    )
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    drv = DhanBarDriver(
        adapter,
        symbols or ["RELIANCE"],
        kw.pop("interval", "1m"),
        "http://honba:8080/",
        "SECRET-HONBA",
        http_post=server,
        clock=clock,
        sleep=sleep,
        **kw,
    )
    return drv, adapter, ws, sleeps


async def start(drv: DhanBarDriver, adapter: DhanAdapter) -> DhanFeedClient:
    await adapter.connect()
    feed = adapter.feed
    assert feed is not None
    feed.add_tick_listener(drv.aggregator.on_tick)
    return feed


async def feed_minutes(feed: DhanFeedClient, minutes: range, base: int = 1000) -> None:
    for m in minutes:
        await feed._on_message(quote_pkt(100.0 + m, base + m * 10, m + 0.1))
        await feed._on_message(quote_pkt(101.0 + m, base + m * 10 + 5, m + 0.5))


class TestSymbols:
    def test_parse_specs(self) -> None:
        assert parse_symbol_spec("reliance") == (InstrumentId("RELIANCE", "NSE"), None)
        assert parse_symbol_spec("TCS.BSE") == (InstrumentId("TCS", "BSE"), None)
        assert parse_symbol_spec("FOO.NSE=777") == (InstrumentId("FOO", "NSE"), "777")
        with pytest.raises(ValueError):
            parse_symbol_spec("FOO=abc")

    def test_unknown_symbol_is_a_startup_error(self) -> None:
        with pytest.raises(ValueError, match="security id"):
            make(Server(), Clock(), ["NOSUCH"])

    def test_explicit_security_id_registers_instrument(self) -> None:
        _drv, adapter, *_ = make(Server(), Clock(), ["FOO.NSE=777"])
        assert adapter._instruments.resolve_to_dhan(InstrumentId("FOO", "NSE")) == ("777", "NSE_EQ")


class TestPosting:
    async def test_only_closed_bars_posted_to_bars_endpoint_with_bearer(self) -> None:
        server, clock = Server(), Clock()
        drv, adapter, *_ = make(server, clock)
        feed = await start(drv, adapter)
        await feed_minutes(feed, range(4))  # minutes 0 (partial, dropped), 1, 2, 3
        clock.at(3.5)
        n = await drv.step()
        # minute 3 is still forming; minutes 1 and 2 are proven closed by later ticks
        assert n == 2
        url, token, payload = server.calls[0]
        assert url == "http://honba:8080/bars" and token == "SECRET-HONBA"
        starts = [b["ts_event"]["unix_nanos"] for b in payload["bars"]]
        assert starts == [str(epoch(1) * 10**9), str(epoch(2) * 10**9)]
        b = payload["bars"][0]
        assert b["bar_type"]["instrument_id"] == {"symbol": "RELIANCE", "exchange": "NSE"}
        assert (b["open"], b["close"]) == (101.0, 102.0)
        assert b["volume"] == 10.0  # cum 1005 -> 1010 -> 1015 across the minute

    async def test_clock_closes_last_bar_after_grace(self) -> None:
        server, clock = Server(), Clock()
        drv, adapter, *_ = make(server, clock)
        feed = await start(drv, adapter)
        await feed_minutes(feed, range(3))
        clock.at(3.0)
        assert await drv.step() == 1  # minute 1 posted; minute 2 within grace
        clock.at(3.1)
        assert await drv.step() == 1  # minute 2 now closed by clock

    async def test_each_bar_posted_once(self) -> None:
        server, clock = Server(), Clock()
        drv, adapter, *_ = make(server, clock)
        feed = await start(drv, adapter)
        await feed_minutes(feed, range(3))
        clock.at(10)
        await drv.step()
        await drv.step()
        starts = [b["ts_event"]["unix_nanos"] for b in server.bars]
        assert len(starts) == len(set(starts)) == 2

    async def test_batches_capped_at_1000(self) -> None:
        server, clock = Server(), Clock()
        drv, *_ = make(server, clock)
        from honba_dhan.aggregator import ClosedBar

        inst = InstrumentId("RELIANCE", "NSE")
        t0 = epoch(0) * 10**9
        drv._enqueue([ClosedBar(inst, t0 + i * 60 * 10**9, 1, 1, 1, 1, 0) for i in range(2500)])
        assert await drv._flush() == 2500
        assert [len(p["bars"]) for _, _, p in server.calls] == [MAX_BATCH, MAX_BATCH, 500]
        ts = [int(b["ts_event"]["unix_nanos"]) for b in server.bars]
        assert ts == sorted(ts)

    async def test_watermark_blocks_reposting_old_bars(self) -> None:
        server, clock = Server(), Clock()
        drv, *_ = make(server, clock)
        from honba_dhan.aggregator import ClosedBar

        inst = InstrumentId("RELIANCE", "NSE")
        t = epoch(5) * 10**9
        drv._enqueue([ClosedBar(inst, t, 1, 1, 1, 1, 0)])
        await drv._flush()
        drv._enqueue([ClosedBar(inst, t, 1, 1, 1, 1, 0), ClosedBar(inst, t - 1, 1, 1, 1, 1, 0)])
        assert await drv._flush() == 0
        assert len(server.calls) == 1


class TestErrorPolicy:
    async def _one_bar(self, server: Server, **kw: Any):
        clock = Clock()
        drv, _adapter, _ws, sleeps = make(server, clock, **kw)
        from honba_dhan.aggregator import ClosedBar

        drv._enqueue([ClosedBar(InstrumentId("RELIANCE", "NSE"), epoch(1) * 10**9, 1, 1, 1, 1, 0)])
        return drv, sleeps

    async def test_429_backs_off_exponentially_then_succeeds(self) -> None:
        server = Server([429, 429, 200])
        drv, sleeps = await self._one_bar(server, backoff_seconds=0.5)
        assert await drv._flush() == 1
        assert sleeps == [0.5, 1.0]
        assert len(server.calls) == 3

    async def test_429_exhaustion_keeps_bars_for_next_cycle(self) -> None:
        server = Server([429] * 3)
        drv, sleeps = await self._one_bar(server, max_retries=2, backoff_seconds=1.0)
        assert await drv._flush() == 0
        assert sleeps == [1.0, 2.0]
        assert len(drv._pending) == 1
        assert await drv._flush() == 1  # later cycle succeeds, nothing lost

    async def test_503_is_fatal(self) -> None:
        drv, _ = await self._one_bar(Server([503]))
        with pytest.raises(IngestFatalError, match="--bar-ingest"):
            await drv._flush()

    @pytest.mark.parametrize("status", [400, 401, 413, 500])
    async def test_other_rejections_are_fatal(self, status: int) -> None:
        drv, _ = await self._one_bar(Server([status]))
        with pytest.raises(IngestFatalError, match=str(status)):
            await drv._flush()


class TestRun:
    async def test_run_subscribes_streams_and_disconnects(self) -> None:
        server, clock = Server(), Clock()
        drv, adapter, ws, _sleeps = make(server, clock)
        pumped = {"n": 0}

        async def sleep(s: float) -> None:
            pumped["n"] += 1
            feed = adapter.feed
            assert feed is not None
            if pumped["n"] == 1:
                await feed_minutes(feed, range(3))
                clock.at(10)
            else:
                drv.stop()

        drv._sleep = sleep
        await drv.run()
        assert ws.sent[0]["RequestCode"] == 17
        assert len(server.bars) == 2
        assert not adapter.is_connected()

    async def test_fatal_error_still_disconnects(self) -> None:
        server, clock = Server([503]), Clock()
        drv, adapter, *_ = make(server, clock)

        async def sleep(_s: float) -> None:
            feed = adapter.feed
            assert feed is not None
            await feed_minutes(feed, range(3))
            clock.at(10)

        drv._sleep = sleep
        with pytest.raises(IngestFatalError):
            await drv.run()
        assert not adapter.is_connected()


class TestCli:
    def test_parser_defaults(self) -> None:
        a = build_parser().parse_args(["live", "--symbols", "RELIANCE"])
        assert (a.interval, a.token_env, a.backfill) == ("1m", "HONBA_TOKEN", False)

    def test_missing_honba_token_exits_2(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        monkeypatch.delenv("HONBA_TOKEN", raising=False)
        assert main(["live", "--symbols", "RELIANCE"]) == 2
        assert "HONBA_TOKEN" in capsys.readouterr().err

    def test_missing_dhan_credentials_exits_2_without_leaking(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setenv("HONBA_TOKEN", "tok-xyz")
        monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
        monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("DHAN_MODE", raising=False)
        assert main(["live", "--symbols", "RELIANCE"]) == 2
        err = capsys.readouterr().err
        assert "DHAN_CLIENT_ID" in err and "tok-xyz" not in err
