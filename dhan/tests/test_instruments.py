"""Tests for Dhan instrument master and security resolution (M2)."""

from __future__ import annotations

from honba.domain.instrument import InstrumentId

from honba_dhan.instruments import DhanInstrumentMaster


class TestDhanInstruments:
    def test_default_seeded_instruments(self) -> None:
        master = DhanInstrumentMaster()
        rel_id = InstrumentId("RELIANCE", "NSE")
        resolved = master.resolve_to_dhan(rel_id)
        assert resolved == ("1333", "NSE_EQ")

        inst = master.get_instrument(rel_id)
        assert inst is not None
        assert inst.instrument_id == rel_id
        assert inst.lot_size == 1.0
        assert inst.tick_size == 0.05

        back_resolved = master.resolve_from_dhan("1333", "NSE_EQ")
        assert back_resolved == rel_id

    def test_search_instruments(self) -> None:
        master = DhanInstrumentMaster()
        results = master.search_instruments("TCS")
        assert len(results) >= 1
        assert any(i.instrument_id == InstrumentId("TCS", "NSE") for i in results)

    def test_load_csv_data(self) -> None:
        csv_sample = """SEM_SMST_SECURITY_ID,SEM_EXM_EXCH_ID,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_TICK_SIZE
99999,NSE,TESTSYMBOL,10,0.05
88888,BSE,TESTBSE,1,0.01
"""
        master = DhanInstrumentMaster()
        count = master.load_csv_data(csv_sample)
        assert count == 2

        test_id = InstrumentId("TESTSYMBOL", "NSE")
        resolved = master.resolve_to_dhan(test_id)
        assert resolved == ("99999", "NSE_EQ")

        test_bse_id = InstrumentId("TESTBSE", "BSE")
        resolved_bse = master.resolve_to_dhan(test_bse_id)
        assert resolved_bse == ("88888", "BSE_EQ")
