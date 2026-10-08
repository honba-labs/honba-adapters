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
