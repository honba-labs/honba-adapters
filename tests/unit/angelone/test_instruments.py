"""Tests for AngelOneInstrumentMaster token and symbol resolution."""

from __future__ import annotations

from honba.domain.instrument import InstrumentId

from honba_adapters.angelone.instruments import AngelOneInstrumentMaster

RELIANCE = InstrumentId("RELIANCE", "NSE")
INFY = InstrumentId("INFY", "NSE")


class TestAngelOneInstruments:
    def test_pre_seeded_resolution(self) -> None:
        master = AngelOneInstrumentMaster()
        assert master.resolve_token(RELIANCE) == "2885"
        assert master.resolve_token(INFY) == "1594"

        inst = master.resolve_instrument_id("NSE", "2885")
        assert inst == RELIANCE

    def test_search_instruments(self) -> None:
        master = AngelOneInstrumentMaster()
        matches = master.search("RELIANCE")
        assert any(i.instrument_id == RELIANCE for i in matches)

    def test_load_from_records(self) -> None:
        master = AngelOneInstrumentMaster()
        records = [
            {
                "token": "9999",
                "symbol": "CUSTOM-EQ",
                "name": "CUSTOM INSTRUMENT",
                "exch_seg": "NSE",
                "lotsize": "1",
                "tick_size": "5.0",
            }
        ]
        count = master.load_from_records(records)
        assert count == 1
        assert master.resolve_token(InstrumentId("CUSTOM", "NSE")) == "9999"
