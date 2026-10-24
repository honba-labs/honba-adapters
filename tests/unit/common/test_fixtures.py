"""Recorded broker responses must load from an adapter's own ``tests/fixtures/``.

The docs promise an adapter can say ``load_fixture("dhan_quote.json")``; this is that promise,
tested against the one thing every broker has.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from honba_adapters.common import fixtures_dir, load_fixture

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _stage(root: Path, relative_path: str, text: str) -> Path:
    path = root / "tests" / "fixtures" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_a_recorded_response_from_the_calling_tests_fixture_dir() -> None:
    raw = load_fixture("quote_response.json")

    assert raw["data"]["tradingsymbol"] == "RELIANCE-EQ"
    assert raw["data"]["bid"] == pytest.approx(2450.45)


def test_a_nested_relative_path_resolves_inside_the_fixture_dir(tmp_path: Path) -> None:
    nested = _stage(tmp_path, "broker/depth_response.json", json.dumps({"data": {"levels": 5}}))

    assert load_fixture("broker/depth_response.json", start=tmp_path) == {"data": {"levels": 5}}
    assert nested.is_relative_to(tmp_path / "tests" / "fixtures")


def test_the_caller_supplied_start_wins_over_the_caller_frame(tmp_path: Path) -> None:
    _stage(tmp_path, "quote_response.json", '{"from": "elsewhere"}')

    assert load_fixture("quote_response.json", start=tmp_path) == {"from": "elsewhere"}


def test_fixtures_dir_is_the_calling_tests_fixture_dir() -> None:
    assert fixtures_dir() == FIXTURES


def test_a_missing_fixture_names_every_directory_it_searched() -> None:
    with pytest.raises(FileNotFoundError) as caught:
        load_fixture("no_such_response.json")

    message = str(caught.value)
    assert "no_such_response.json" in message
    assert "tests/fixtures" in message


def test_a_missing_fixture_dir_says_it_looked_outward(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        load_fixture("anything.json", start=tmp_path / "absent")

    assert "tests/fixtures" in str(caught.value)


def test_an_absolute_path_is_rejected() -> None:
    absolute = str(FIXTURES / "quote_response.json")

    with pytest.raises(ValueError, match="not an absolute path"):
        load_fixture(absolute)


def test_malformed_json_fails_naming_the_fixture_that_holds_it(tmp_path: Path) -> None:
    _stage(tmp_path, "not_json.json", "{oops")

    with pytest.raises(json.JSONDecodeError) as caught:
        load_fixture("not_json.json", start=tmp_path)

    assert "not_json.json" in str(caught.value)


@pytest.mark.asyncio
async def test_recorded_websocket_client() -> None:
    from honba_adapters.common import RecordedWebSocketClient

    frames = [{"type": "quote", "price": 100}, b"\x01\x02\x03", "PING"]
    client = RecordedWebSocketClient(frames)

    assert not client.is_connected
    await client.connect()
    assert client.is_connected

    await client.send("SUBSCRIBE 123")
    assert client.sent_messages == ["SUBSCRIBE 123"]

    f1 = await client.receive()
    assert json.loads(f1) == {"type": "quote", "price": 100}

    f2 = await client.receive()
    assert f2 == b"\x01\x02\x03"

    f3 = await client.receive()
    assert f3 == "PING"

    with pytest.raises(StopAsyncIteration):
        await client.receive()

    await client.disconnect()
    assert not client.is_connected


@pytest.mark.asyncio
async def test_recorded_websocket_client_context_manager() -> None:
    from honba_adapters.common import RecordedWebSocketClient

    frames = ["hello", "world"]
    async with RecordedWebSocketClient(frames) as client:
        assert client.is_connected
        received = []
        async for frame in client:
            received.append(frame)
        assert received == ["hello", "world"]
    assert not client.is_connected
