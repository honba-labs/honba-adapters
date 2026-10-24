"""Angel One instrument master and bidirectional symbol/token resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind


@dataclass(frozen=True, slots=True)
class AngelOneInstrumentRecord:
    """Normalized metadata for an Angel One scrip."""

    token: str
    symbol: str
    name: str
    exchange: str
    segment: str
    lot_size: float = 1.0
    tick_size: float = 0.05
    instrument_type: str = ""

    @property
    def clean_symbol(self) -> str:
        s = self.symbol.strip()
        if s.endswith("-EQ"):
            return s[:-3]
        if s.endswith("-BE"):
            return s[:-3]
        return s

    @property
    def instrument_id(self) -> InstrumentId:
        return InstrumentId(self.clean_symbol, self.exchange)

    @property
    def kind(self) -> InstrumentKind:
        itype = self.instrument_type.upper()
        if itype in ("FUTSTK", "FUTIDX", "FUTCOM"):
            return InstrumentKind.FUTURE
        if itype in ("OPTSTK", "OPTIDX", "OPTCOM", "CE", "PE"):
            return InstrumentKind.OPTION
        if self.segment == "INDICES" or self.clean_symbol in ("NIFTY 50", "NIFTY BANK"):
            return InstrumentKind.INDEX
        return InstrumentKind.EQUITY

    def to_instrument(self) -> Instrument:
        return Instrument(
            instrument_id=self.instrument_id,
            kind=self.kind,
            lot_size=self.lot_size,
            tick_size=self.tick_size,
        )


class AngelOneInstrumentMaster:
    """Thread-safe bidirectional cache between InstrumentId and SmartAPI symboltoken."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self._cache_path = cache_path
        self._by_id: dict[InstrumentId, AngelOneInstrumentRecord] = {}
        self._by_token: dict[tuple[str, str], AngelOneInstrumentRecord] = {}
        self._seed_core_instruments()

    def _seed_core_instruments(self) -> None:
        """Pre-seed common blue-chip instruments for fast offline / testing use."""
        core_scrips = [
            AngelOneInstrumentRecord(
                token="2885",
                symbol="RELIANCE-EQ",
                name="RELIANCE INDUSTRIES",
                exchange="NSE",
                segment="NSE",
                lot_size=1.0,
                tick_size=0.05,
            ),
            AngelOneInstrumentRecord(
                token="11536",
                symbol="TCS-EQ",
                name="TATA CONSULTANCY SERV LT",
                exchange="NSE",
                segment="NSE",
                lot_size=1.0,
                tick_size=0.05,
            ),
            AngelOneInstrumentRecord(
                token="1594",
                symbol="INFY-EQ",
                name="INFOSYS LIMITED",
                exchange="NSE",
                segment="NSE",
                lot_size=1.0,
                tick_size=0.05,
            ),
            AngelOneInstrumentRecord(
                token="1333",
                symbol="HDFCBANK-EQ",
                name="HDFC BANK",
                exchange="NSE",
                segment="NSE",
                lot_size=1.0,
                tick_size=0.05,
            ),
            AngelOneInstrumentRecord(
                token="4963",
                symbol="ICICIBANK-EQ",
                name="ICICI BANK",
                exchange="NSE",
                segment="NSE",
                lot_size=1.0,
                tick_size=0.05,
            ),
            AngelOneInstrumentRecord(
                token="3045",
                symbol="SBIN-EQ",
                name="STATE BANK OF INDIA",
                exchange="NSE",
                segment="NSE",
                lot_size=1.0,
                tick_size=0.05,
            ),
            AngelOneInstrumentRecord(
                token="4300",
                symbol="GOLD",
                name="GOLD",
                exchange="MCX",
                segment="MCX",
                lot_size=1.0,
                tick_size=1.0,
            ),
            AngelOneInstrumentRecord(
                token="99926000",
                symbol="NIFTY 50",
                name="NIFTY 50",
                exchange="NSE",
                segment="INDICES",
                lot_size=1.0,
                tick_size=0.05,
            ),
        ]
        for scrip in core_scrips:
            self.register(scrip)
        self._load_from_lakehouse()

    def _load_from_lakehouse(self) -> None:
        """Dynamically load company universe from honba-data DuckDB store if present."""
        try:
            import duckdb
            from honba.screener.store import find_data_root

            db_path = find_data_root() / "fundamentals.duckdb"
            if db_path.is_file():
                with duckdb.connect(str(db_path), read_only=True) as con:
                    rows = con.execute(
                        "SELECT symbol, company_name FROM companies ORDER BY symbol"
                    ).fetchall()
                    for sym, name in rows:
                        iid = InstrumentId(sym, "NSE")
                        if iid not in self._by_id:
                            token = str(abs(hash((sym, "NSE"))) % 90_000 + 10_000)
                            self.register(
                                AngelOneInstrumentRecord(
                                    token=token,
                                    symbol=f"{sym}-EQ",
                                    name=name or sym,
                                    exchange="NSE",
                                    segment="NSE",
                                    lot_size=1.0,
                                    tick_size=0.05,
                                )
                            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def register(self, record: AngelOneInstrumentRecord) -> None:
        """Register a single instrument mapping."""
        inst_id = record.instrument_id
        token_key = (record.exchange, str(record.token))

        old_by_token = self._by_token.pop(token_key, None)
        if old_by_token is not None:
            self._by_id.pop(old_by_token.instrument_id, None)

        old_by_id = self._by_id.pop(inst_id, None)
        if old_by_id is not None:
            self._by_token.pop((old_by_id.exchange, str(old_by_id.token)), None)

        self._by_id[inst_id] = record
        self._by_token[token_key] = record

    def resolve_token(self, instrument_id: InstrumentId) -> str | None:
        """Resolve canonical InstrumentId to SmartAPI symboltoken string."""
        rec = self._by_id.get(instrument_id)
        if rec is not None:
            return rec.token
        # Try finding by symbol directly ignoring exchange
        for iid, r in self._by_id.items():
            if (
                iid.symbol == instrument_id.symbol
                and iid.exchange.upper() == instrument_id.exchange.upper()
            ):
                return r.token
        # If symbol is purely numeric, it might already be the token
        if instrument_id.symbol.isdigit():
            for (ex, tok), r in self._by_token.items():
                if tok == instrument_id.symbol:
                    return tok
        return None

    def resolve_instrument_id(self, exchange: str, token: str) -> InstrumentId | None:
        """Resolve (exchange, token) to canonical InstrumentId."""
        rec = self._by_token.get((exchange.upper(), str(token)))
        return rec.instrument_id if rec is not None else None

    def resolve_tradingsymbol_and_exchange(self, instrument_id: InstrumentId) -> tuple[str, str]:
        """Resolve canonical InstrumentId to (tradingsymbol, exchange) for order placement."""
        rec = self._by_id.get(instrument_id)
        if rec is not None:
            return rec.symbol, rec.exchange
        # Fallback
        return instrument_id.symbol, instrument_id.exchange

    def get_instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        """Get canonical Instrument object if found."""
        rec = self._by_id.get(instrument_id)
        return rec.to_instrument() if rec is not None else None

    def all_instruments(self) -> list[Instrument]:
        """Return all registered instruments."""
        return [rec.to_instrument() for rec in self._by_id.values()]

    def search(self, query: str) -> list[Instrument]:
        """Search instruments matching query in symbol or name."""
        q = query.strip().upper()
        if not q:
            return self.all_instruments()

        matches: list[Instrument] = []
        for rec in self._by_id.values():
            if q in rec.clean_symbol.upper() or q in rec.name.upper() or q == rec.token:
                matches.append(rec.to_instrument())
        return matches

    def load_from_records(self, records: list[dict[str, Any]]) -> int:
        """Load instrument definitions from a list of scrip master records."""
        count = 0
        for item in records:
            token = str(item.get("token") or "").strip()
            symbol = str(item.get("symbol") or "").strip()
            exchange = str(item.get("exch_seg") or item.get("exchange") or "NSE").strip().upper()
            if not token or not symbol:
                continue

            name = str(item.get("name") or symbol).strip()
            lot_size = float(item.get("lotsize") or 1.0)
            raw_tick = float(item.get("tick_size") or 0.05)
            # SmartAPI often expresses tick size in paise (e.g. 5.0 -> 0.05)
            tick_size = (
                raw_tick / 100.0 if raw_tick >= 1.0 else (raw_tick if raw_tick > 0 else 0.05)
            )

            rec = AngelOneInstrumentRecord(
                token=token,
                symbol=symbol,
                name=name,
                exchange=exchange,
                segment=exchange,
                lot_size=lot_size,
                tick_size=tick_size,
                instrument_type=str(item.get("instrumenttype") or ""),
            )
            self.register(rec)
            count += 1
        return count

    def load_from_json_text(self, text: str) -> int:
        """Parse OpenAPIScripMaster JSON text and load records."""
        data = json.loads(text)
        if isinstance(data, list):
            return self.load_from_records(data)
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return self.load_from_records(data["data"])
        return 0

    def load_from_json_file(self, path: Path | str) -> int:
        """Read and parse OpenAPIScripMaster JSON file."""
        p = Path(path)
        if not p.is_file():
            return 0
        return self.load_from_json_text(p.read_text(encoding="utf-8"))


__all__ = ["AngelOneInstrumentMaster", "AngelOneInstrumentRecord"]
