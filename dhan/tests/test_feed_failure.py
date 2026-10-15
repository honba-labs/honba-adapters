"""Feed auth rejection and reconnect cap, with a fake websockets.connect (no network)."""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import honba_adapters_shared.websocket as shared_ws
import httpx
import pytest
from honba.adapters.errors import AdapterFatalError
from honba_adapters_shared.errors import AuthenticationError
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from honba_dhan import DhanAdapter, DhanConfig
from honba_dhan.driver import DhanBarDriver
from honba_dhan.http import DH902_MESSAGE
from honba_dhan.instruments import DhanInstrumentMaster
from honba_dhan.websocket import DhanFeedClient, is_auth_rejection


def _cfg(**kw: Any) -> DhanConfig:
    return DhanConfig.live(
        client_id="c", access_token="t", feed_min_backoff=0.001, feed_max_backoff=0.002, **kw
    )


def _reject(status: int) -> InvalidStatus:
    return InvalidStatus(Response(status, "Forbidden", Headers()))


async def _until(pred, timeout: float = 3.0) -> None:
    async def loop() -> None:
        while not pred():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(loop(), timeout)


class FakeConnect:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def __call__(self, *a: Any, **k: Any) -> Any:
        self.calls += 1
        raise self.exc


def test_is_auth_rejection() -> None:
    assert is_auth_rejection(_reject(401)) and is_auth_rejection(_reject(403))
    assert not is_auth_rejection(_reject(500))
    assert not is_auth_rejection(OSError("boom"))


@pytest.mark.parametrize("status", [401, 403])
async def test_upgrade_rejection_stops_reconnecting(monkeypatch, status: int) -> None:
    fake = FakeConnect(_reject(status))
    monkeypatch.setattr(shared_ws.websockets, "connect", fake)
    feed = DhanFeedClient(_cfg(), DhanInstrumentMaster())
    await feed.start()
    await _until(lambda: feed.failure is not None)
    await asyncio.sleep(0.05)  # would reconnect many times if it were not halted
    await feed.stop()
    assert fake.calls == 1
    assert isinstance(feed.failure, AuthenticationError)
    assert str(feed.failure) == DH902_MESSAGE


async def test_other_errors_capped_at_max_reconnects(monkeypatch) -> None:
    fake = FakeConnect(OSError("network down"))
    monkeypatch.setattr(shared_ws.websockets, "connect", fake)
    feed = DhanFeedClient(_cfg(), DhanInstrumentMaster(), max_reconnects=3)
    await feed.start()
    await _until(lambda: feed.failure is not None)
    await asyncio.sleep(0.05)
    await feed.stop()
    assert fake.calls == 4  # first attempt + 3 reconnects
    assert isinstance(feed.failure, AdapterFatalError)
    assert "3 consecutive reconnect attempts" in str(feed.failure)


async def test_disconnect_packet_with_auth_code_fails_feed() -> None:
    class WS:
        is_connected = True
        halted = False

        def halt_reconnects(self) -> None:
            self.halted = True

    ws = WS()
    feed = DhanFeedClient(_cfg(), DhanInstrumentMaster(), ws=ws)
    pkt = struct.pack("<BHBI", 50, 10, 0, 0) + struct.pack("<h", 807)
    await feed._on_message(pkt)
    assert ws.halted and isinstance(feed.failure, AuthenticationError)
    with pytest.raises(AuthenticationError):
        feed.raise_if_failed()


async def test_non_auth_disconnect_packet_is_not_terminal() -> None:
    feed = DhanFeedClient(_cfg(), DhanInstrumentMaster(), ws=object())
    await feed._on_message(struct.pack("<BHBI", 50, 10, 0, 0) + struct.pack("<h", 805))
    assert feed.failure is None


async def test_driver_run_raises_fatal_when_feed_rejected(monkeypatch) -> None:
    class FakeWS:
        is_connected = True

        async def start(self) -> None: ...
        async def stop(self) -> None: ...
        async def send_json(self, payload: Any) -> None: ...
        def halt_reconnects(self) -> None: ...

    adapter = DhanAdapter(
        _cfg(),
        http_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        feed_factory=lambda cfg, inst: DhanFeedClient(cfg, inst, ws=FakeWS()),
    )

    async def sleep(s: float) -> None:
        await asyncio.sleep(0)

    drv = DhanBarDriver(
        adapter,
        ["RELIANCE"],
        "1m",
        "http://h:1",
        "tok",
        http_post=lambda *a: (200, {}),
        sleep=sleep,
    )
    orig = adapter.connect

    async def connect() -> None:
        await orig()
        assert adapter.feed is not None
        adapter.feed._fail_auth()  # as if the upgrade/disconnect packet had been rejected

    adapter.connect = connect  # type: ignore[method-assign]
    with pytest.raises(AuthenticationError, match="Dhan rejected the token"):
        await drv.run()
