"""Instrument master caching and security resolution for Dhan (M2).

Dhan identifies assets by integer ``security_id`` (e.g. "1333") and ``exchange_segment``
(e.g. "NSE_EQ"), whereas Honba uses canonical ``InstrumentId(symbol, exchange)``.

This module provides bidirectional $O(1)$ lookups backed by an in-memory or SQLite cache,
with seed fixtures for core symbols and CSV import support for the full Dhan scrip master.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind

from honba_dhan.constants import EXCHANGE_TO_SEGMENT, SEGMENT_TO_EXCHANGE

__all__ = ["DhanInstrumentInfo", "DhanInstrumentMaster"]

# Default seed instruments so offline/paper tests work immediately
_DEFAULT_SEEDS = [
    ("1333", "NSE_EQ", "RELIANCE", "RELIANCE", 1.0, 0.05, "EQUITY", "NSE"),
    ("11536", "NSE_EQ", "TCS", "TCS", 1.0, 0.05, "EQUITY", "NSE"),
    ("1594", "NSE_EQ", "INFY", "INFY", 1.0, 0.05, "EQUITY", "NSE"),
    ("1330", "NSE_EQ", "HDFCBANK", "HDFCBANK", 1.0, 0.05, "EQUITY", "NSE"),
    ("4963", "NSE_EQ", "ICICIBANK", "ICICIBANK", 1.0, 0.05, "EQUITY", "NSE"),
    ("500325", "BSE_EQ", "RELIANCE", "RELIANCE", 1.0, 0.05, "EQUITY", "BSE"),
]


@dataclass(frozen=True, slots=True)
class DhanInstrumentInfo:
    """Internal Dhan security metadata."""

    security_id: str
    exchange_segment: str
    symbol: str
    lot_size: float = 1.0
    tick_size: float = 0.05
    kind: InstrumentKind = InstrumentKind.EQUITY

    @property
    def exchange(self) -> str:
        seg_info = SEGMENT_TO_EXCHANGE.get(self.exchange_segment)
        return seg_info[0] if seg_info else "NSE"

    @property
    def instrument_id(self) -> InstrumentId:
        return InstrumentId(self.symbol, self.exchange)

    def to_instrument(self) -> Instrument:
        return Instrument(
            instrument_id=self.instrument_id,
            kind=self.kind,
            lot_size=self.lot_size,
            tick_size=self.tick_size,
        )


class DhanInstrumentMaster:
    """Manages mapping between Honba InstrumentId and Dhan (security_id, exchange_segment)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = db_path
        self._by_id: dict[InstrumentId, DhanInstrumentInfo] = {}
        self._by_dhan_key: dict[tuple[str, str], DhanInstrumentInfo] = {}

        # Pre-seed with default core instruments
        for row in _DEFAULT_SEEDS:
            self.add_instrument(
                security_id=row[0],
                exchange_segment=row[1],
                symbol=row[2],
                lot_size=row[4],
                tick_size=row[5],
                kind=InstrumentKind.EQUITY,
            )

    def add_instrument(
        self,
        security_id: str,
        exchange_segment: str,
        symbol: str,
        lot_size: float = 1.0,
        tick_size: float = 0.05,
        kind: InstrumentKind = InstrumentKind.EQUITY,
    ) -> DhanInstrumentInfo:
        """Register one instrument in memory."""
        info = DhanInstrumentInfo(
            security_id=str(security_id),
            exchange_segment=exchange_segment,
            symbol=symbol.upper(),
            lot_size=float(lot_size),
            tick_size=float(tick_size),
            kind=kind,
        )
        self._by_id[info.instrument_id] = info
        self._by_dhan_key[(info.security_id, info.exchange_segment)] = info
        return info

    def resolve_to_dhan(self, instrument_id: InstrumentId) -> tuple[str, str] | None:
        """Resolve canonical InstrumentId to Dhan (security_id, exchange_segment)."""
        info = self._by_id.get(instrument_id)
        if info:
            return info.security_id, info.exchange_segment

        # Fallback heuristic: check if symbol exists under expected segment
        expected_seg = EXCHANGE_TO_SEGMENT.get((instrument_id.exchange, "EQUITY"), "NSE_EQ")
        # Check if instrument_id.symbol looks like an integer security_id
        if instrument_id.symbol.isdigit():
            return instrument_id.symbol, expected_seg

        return None

    def resolve_from_dhan(self, security_id: str, exchange_segment: str) -> InstrumentId | None:
        """Resolve Dhan security_id and segment to canonical InstrumentId."""
        info = self._by_dhan_key.get((str(security_id), exchange_segment))
        if info:
            return info.instrument_id
        # Fallback
        seg_info = SEGMENT_TO_EXCHANGE.get(exchange_segment)
        exchange = seg_info[0] if seg_info else "NSE"
        return InstrumentId(str(security_id), exchange)

    def get_instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        """Get canonical Instrument object if found."""
        info = self._by_id.get(instrument_id)
        return info.to_instrument() if info else None

    def all_instruments(self) -> list[Instrument]:
        """Return all registered instruments."""
        return [info.to_instrument() for info in self._by_id.values()]

    def search_instruments(self, query: str) -> list[Instrument]:
        """Search instruments matching symbol prefix or substring."""
        q = query.strip().upper()
        if not q:
            return []
        matches: list[Instrument] = []
        for info in self._by_id.values():
            if q in info.symbol or q in info.security_id:
                matches.append(info.to_instrument())
        return matches

    def load_csv_data(self, csv_text_or_file: str | io.TextIOBase) -> int:
        """Load instruments from Dhan api-scrip-master CSV."""
        if isinstance(csv_text_or_file, str):
            f = io.StringIO(csv_text_or_file)
        else:
            f = csv_text_or_file

        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            sec_id = row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID")
            seg = row.get("SEM_EXM_EXCH_ID") or row.get("EXCHANGE_SEGMENT")
            symbol = (
                row.get("SEM_TRADING_SYMBOL") or row.get("SYMBOL") or row.get("SEM_CUSTOM_SYMBOL")
            )
            if not sec_id or not symbol:
                continue

            lot_size = float(row.get("SEM_LOT_UNITS") or 1.0)
            tick_size = float(row.get("SEM_TICK_SIZE") or 0.05)

            # Map segment name
            seg_code = "NSE_EQ"
            if seg:
                seg_upper = seg.upper()
                if "NSE" in seg_upper and "FNO" in seg_upper:
                    seg_code = "NSE_FNO"
                elif "BSE" in seg_upper and "FNO" in seg_upper:
                    seg_code = "BSE_FNO"
                elif "BSE" in seg_upper:
                    seg_code = "BSE_EQ"
                elif "MCX" in seg_upper:
                    seg_code = "MCX_COMM"

            self.add_instrument(
                security_id=sec_id,
                exchange_segment=seg_code,
                symbol=symbol,
                lot_size=lot_size,
                tick_size=tick_size,
            )
            count += 1
        return count
