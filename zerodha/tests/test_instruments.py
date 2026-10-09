"""Tests for Zerodha instrument master and token resolution (M3)."""

from __future__ import annotations

from honba.domain.instrument import InstrumentId

from honba_zerodha.instruments import ZerodhaInstrumentMaster


def test_core_instruments_pre_seeded() -> None:
    master = ZerodhaInstrumentMaster()
    reliance_id = InstrumentId("RELIANCE", "NSE")
    token = master.resolve_token(reliance_id)
    assert token == 738561

    resolved_id = master.resolve_instrument_id(738561)
    assert resolved_id == reliance_id

    symbol, exchange = master.resolve_tradingsymbol_and_exchange(reliance_id)
    assert symbol == "RELIANCE"
    assert exchange == "NSE"


def test_search_instruments() -> None:
    master = ZerodhaInstrumentMaster()
    results = master.search_instruments("TCS")
    assert any(inst.instrument_id == InstrumentId("TCS", "NSE") for inst in results)


def test_load_from_csv() -> None:
    csv_data = """instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
256265,26000,NIFTY 50,NIFTY 50,0.0,,,0.05,1,EQ,INDICES,NSE
895745,3499,TATAMOTORS,TATA MOTORS LIMITED,0.0,,,0.05,1,EQ,NSE,NSE
"""
    master = ZerodhaInstrumentMaster()
    count = master.load_from_csv(csv_data)
    assert count == 2

    tatamotors_id = InstrumentId("TATAMOTORS", "NSE")
    assert master.resolve_token(tatamotors_id) == 895745
    assert master.resolve_instrument_id(895745) == tatamotors_id


_CSV = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,"
    "lot_size,instrument_type,segment,exchange\n"
    "738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n"
    "256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE\n"
)


class TestTokenMapConsistency:
    """Ported from honba-broker-zerodha ``tokens.rs`` golden tests."""

    def test_reinserting_instrument_drops_stale_token(self) -> None:
        from honba_zerodha.instruments import ZerodhaInstrumentRecord

        master = ZerodhaInstrumentMaster()
        inst = InstrumentId("ZZTEST", "NSE")
        master.register(ZerodhaInstrumentRecord(1, "1", "ZZTEST", "Z", "NSE", "NSE"))
        master.register(ZerodhaInstrumentRecord(2, "2", "ZZTEST", "Z", "NSE", "NSE"))
        assert master.resolve_token(inst) == 2
        assert master.resolve_instrument_id(1) is None
        assert master.resolve_instrument_id(2) == inst

    def test_reinserting_token_drops_stale_instrument(self) -> None:
        from honba_zerodha.instruments import ZerodhaInstrumentRecord

        master = ZerodhaInstrumentMaster()
        a, b = InstrumentId("AAA", "NSE"), InstrumentId("BBB", "NSE")
        master.register(ZerodhaInstrumentRecord(5, "5", "AAA", "A", "NSE", "NSE"))
        master.register(ZerodhaInstrumentRecord(5, "5", "BBB", "B", "NSE", "NSE"))
        assert master.resolve_token(a) is None
        assert master.resolve_token(b) == 5
        assert all(i.instrument_id != a for i in master.all_instruments())

    def test_duplicate_csv_rows_keep_last_consistently(self) -> None:
        master = ZerodhaInstrumentMaster()
        dup = _CSV + "999,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n"
        master.load_from_csv(dup)
        assert master.resolve_token(InstrumentId("RELIANCE", "NSE")) == 999
        assert master.resolve_instrument_id(738561) is None

    def test_nifty_row_parses(self) -> None:
        master = ZerodhaInstrumentMaster()
        assert master.load_from_csv(_CSV) == 2
        assert master.resolve_token(InstrumentId("NIFTY 50", "NSE")) == 256265

    def test_strict_mode_rejects_malformed_rows(self) -> None:
        import pytest

        master = ZerodhaInstrumentMaster()
        bad = _CSV + "notanumber,1,X,X,0,,0,0,1,EQ,NSE,NSE\n"
        with pytest.raises(ValueError):
            master.load_from_csv(bad, strict=True)
        # Lenient default still skips the bad row.
        assert ZerodhaInstrumentMaster().load_from_csv(bad) == 2

    def test_missing_required_column_is_an_error(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="instrument_token"):
            ZerodhaInstrumentMaster().load_from_csv("tradingsymbol,exchange\nX,NSE\n")
