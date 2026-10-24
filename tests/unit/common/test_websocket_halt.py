"""halt_reconnects stops a ReconnectingWebSocketClient from retrying (hoisted from honba-dhan)."""

from __future__ import annotations

from honba_adapters.common.websocket import ReconnectingWebSocketClient


def test_reconnects_are_on_by_default_and_halt_turns_them_off() -> None:
    client = ReconnectingWebSocketClient("ws://localhost:1")
    assert client.reconnects_enabled is True
    client.halt_reconnects()
    assert client.reconnects_enabled is False


def test_halt_is_idempotent() -> None:
    client = ReconnectingWebSocketClient("ws://localhost:1", reconnect=False)
    client.halt_reconnects()
    assert client.reconnects_enabled is False
