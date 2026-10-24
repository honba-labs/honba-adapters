"""Market data and subscription flow tests for AngelOneAdapter."""

from __future__ import annotations

import datetime as dt

import pytest
from honba.adapters.errors import AdapterError
from honba.adapters.models import StreamMode
from honba.domain.instrument import InstrumentId

from honba_adapters.angelone import AngelOneAdapter, AngelOneConfig, make_angelone_mock_transport

RELIANCE = InstrumentId("RELIANCE", "NSE")
UNKNOWN = InstrumentId("NONEXISTENT", "NSE")


class TestAngelOneFeedFlow:
    @pytest.mark.asyncio
    async def test_quote_and_depth(self) -> None:
        adapter = AngelOneAdapter(
            AngelOneConfig.paper(), http_transport=make_angelone_mock_transport()
        )
        await adapter.connect()

        quote = await adapter.quote(RELIANCE)
        assert quote.instrument_id == RELIANCE
        assert quote.bid_price > 0
        assert quote.ask_price > 0

        depth = await adapter.depth(RELIANCE, levels=5)
        assert depth.instrument_id == RELIANCE
        assert len(depth.bids) > 0
        assert len(depth.asks) > 0
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_historical_bars(self) -> None:
        adapter = AngelOneAdapter(
            AngelOneConfig.paper(), http_transport=make_angelone_mock_transport()
        )
        await adapter.connect()

        start = dt.datetime(2023, 11, 15, 9, 15, tzinfo=dt.timezone.utc)
        end = dt.datetime(2023, 11, 15, 9, 30, tzinfo=dt.timezone.utc)
        bars = await adapter.historical_bars(RELIANCE, timeframe="1m", start=start, end=end)

        assert len(bars) == 3
        assert bars[0].ts <= bars[1].ts <= bars[2].ts
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_quote_unknown_instrument_raises(self) -> None:
        adapter = AngelOneAdapter(
            AngelOneConfig.paper(), http_transport=make_angelone_mock_transport()
        )
        await adapter.connect()

        with pytest.raises(AdapterError, match="unknown instrument"):
            await adapter.quote(UNKNOWN)
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_subscription_lifecycle(self) -> None:
        adapter = AngelOneAdapter(
            AngelOneConfig.paper(), http_transport=make_angelone_mock_transport()
        )
        await adapter.connect()

        received = []
        sub = await adapter.subscribe((RELIANCE,), mode=StreamMode.QUOTE, callback=received.append)
        assert sub.instruments == (RELIANCE,)
        assert sub.mode is StreamMode.QUOTE
        assert sub.id

        await adapter.unsubscribe(sub.id)

        with pytest.raises(AdapterError, match="unknown subscription"):
            await adapter.unsubscribe("invalid-id")

        await adapter.disconnect()
