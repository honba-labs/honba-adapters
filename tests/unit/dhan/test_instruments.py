"""Tests for Dhan instrument master and security resolution (M2)."""

from __future__ import annotations

from honba.domain.instrument import InstrumentId

from honba_adapters.dhan.instruments import DhanInstrumentMaster


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


_SCRIP_MASTER = """SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_TICK_SIZE,SEM_SERIES
NSE,E,2885,EQUITY,ACMEIND,1,0.05,EQ
NSE,D,51001,FUTSTK,ACMEIND,500,0.05,XX
NSE,D,51002,OPTSTK,ACMEIND,500,0.05,XX
NSE,E,3001,EQUITY,ACMEBE,1,0.05,BE
NSE,E,3002,ETF,ACMEETF,1,0.05,EQ
NSE,I,13,INDEX,NIFTY,1,0.05,
BSE,E,500999,EQUITY,ACMEIND,1,0.01,A
BSE,D,60001,FUTIDX,SENSEX,10,0.05,XX
MCX,M,70001,FUTCOM,GOLD,100,1,XX
"""


class TestScripMasterFilter:
    def test_only_cash_equity_rows_are_loaded(self) -> None:
        m = DhanInstrumentMaster()
        assert m.load_csv_data(_SCRIP_MASTER) == 2
        assert m.resolve_to_dhan(InstrumentId("ACMEIND", "NSE")) == ("2885", "NSE_EQ")
        assert m.resolve_to_dhan(InstrumentId("ACMEIND", "BSE")) == ("500999", "BSE_EQ")
        for sym in ("ACMEBE", "ACMEETF", "NIFTY", "GOLD", "SENSEX"):
            assert m.resolve_to_dhan(InstrumentId(sym, "NSE")) is None
        assert m.resolve_from_dhan("51001", "NSE_EQ") == InstrumentId("51001", "NSE")

    def test_fno_row_does_not_overwrite_equity_symbol(self) -> None:
        m = DhanInstrumentMaster()
        m.load_csv_data(_SCRIP_MASTER)
        info = m.get_instrument(InstrumentId("ACMEIND", "NSE"))
        assert info is not None and info.lot_size == 1.0

    def test_explicit_override_still_wins(self) -> None:
        m = DhanInstrumentMaster()
        m.load_csv_data(_SCRIP_MASTER)
        m.add_instrument(security_id="777", exchange_segment="NSE_EQ", symbol="ACMEIND")
        assert m.resolve_to_dhan(InstrumentId("ACMEIND", "NSE")) == ("777", "NSE_EQ")
