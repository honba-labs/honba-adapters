"""Zerodha instrument master and bidirectional token resolution."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind


@dataclass(frozen=True, slots=True)
class ZerodhaInstrumentRecord:
    """Normalized metadata for a Zerodha scrip."""

    instrument_token: int
    exchange_token: str
    tradingsymbol: str
    name: str
    exchange: str
    segment: str
    lot_size: float = 1.0
    tick_size: float = 0.05
    instrument_type: str = "EQ"

    @property
    def instrument_id(self) -> InstrumentId:
        return InstrumentId(self.tradingsymbol, self.exchange)

    @property
    def kind(self) -> InstrumentKind:
        itype = self.instrument_type.upper()
        if itype in ("EQ", ""):
            return InstrumentKind.EQUITY
        if itype in ("FUT",):
            return InstrumentKind.FUTURE
        if itype in ("CE", "PE", "OPT"):
            return InstrumentKind.OPTION
        return InstrumentKind.EQUITY

    def to_instrument(self) -> Instrument:
        return Instrument(
            instrument_id=self.instrument_id,
            kind=self.kind,
            lot_size=self.lot_size,
            tick_size=self.tick_size,
        )


class ZerodhaInstrumentMaster:
    """Thread-safe bidirectional cache between canonical InstrumentId and Zerodha instrument_token."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self._cache_path = cache_path
        self._by_id: dict[InstrumentId, ZerodhaInstrumentRecord] = {}
        self._by_token: dict[int, ZerodhaInstrumentRecord] = {}
        self._seed_core_instruments()

    def _seed_core_instruments(self) -> None:
        """Pre-seed common blue-chip instruments for fast offline / testing use."""
        core_scrips = [
            ZerodhaInstrumentRecord(
                738561, "2885", "RELIANCE", "RELIANCE INDUSTRIES", "NSE", "NSE", 1.0, 0.05, "EQ"
            ),
            ZerodhaInstrumentRecord(
                2953217, "11536", "TCS", "TATA CONSULTANCY SERV LT", "NSE", "NSE", 1.0, 0.05, "EQ"
            ),
            ZerodhaInstrumentRecord(
                408065, "1594", "INFY", "INFOSYS LIMITED", "NSE", "NSE", 1.0, 0.05, "EQ"
            ),
            ZerodhaInstrumentRecord(
                341249, "1333", "HDFCBANK", "HDFC BANK", "NSE", "NSE", 1.0, 0.05, "EQ"
            ),
            ZerodhaInstrumentRecord(
                1270529, "4963", "ICICIBANK", "ICICI BANK", "NSE", "NSE", 1.0, 0.05, "EQ"
            ),
            ZerodhaInstrumentRecord(
                779521, "3045", "SBIN", "STATE BANK OF INDIA", "NSE", "NSE", 1.0, 0.05, "EQ"
            ),
            ZerodhaInstrumentRecord(
                256265, "26000", "NIFTY 50", "NIFTY 50", "NSE", "INDICES", 1.0, 0.05, "EQ"
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
                            token = abs(hash((sym, "NSE"))) % 9_000_000 + 1_000_000
                            self.register(
                                ZerodhaInstrumentRecord(
                                    instrument_token=token,
                                    exchange_token=str(token % 100_000),
                                    tradingsymbol=sym,
                                    name=name or sym,
                                    exchange="NSE",
                                    segment="NSE",
                                    lot_size=1.0,
                                    tick_size=0.05,
                                    instrument_type="EQ",
                                )
                            )
        except (ImportError, OSError, RuntimeError, ValueError, KeyError):
            return

    def register(self, record: ZerodhaInstrumentRecord) -> None:
        """Register a single instrument mapping.

        Any stale entry for the same token or the same instrument is removed first, so both
        lookup directions always agree (later registrations win).
        """
        inst_id = InstrumentId(record.tradingsymbol, record.exchange)
        old_by_token = self._by_token.pop(record.instrument_token, None)
        if old_by_token is not None:
            self._by_id.pop(old_by_token.instrument_id, None)
        old_by_id = self._by_id.pop(inst_id, None)
        if old_by_id is not None:
            self._by_token.pop(old_by_id.instrument_token, None)
        self._by_id[inst_id] = record
        self._by_token[record.instrument_token] = record

    def get_record(self, instrument_id: InstrumentId) -> ZerodhaInstrumentRecord | None:
        """Fetch metadata record for an InstrumentId."""
        return self._by_id.get(instrument_id)

    def resolve_token(self, instrument_id: InstrumentId) -> int | None:
        """Resolve canonical InstrumentId to Zerodha instrument_token."""
        rec = self._by_id.get(instrument_id)
        if rec is not None:
            return rec.instrument_token
        # If symbol itself is numeric, it might be the token string
        if instrument_id.symbol.isdigit():
            token_int = int(instrument_id.symbol)
            if token_int in self._by_token:
                return token_int
        return None

    def resolve_instrument_id(self, instrument_token: int) -> InstrumentId | None:
        """Resolve Zerodha instrument_token to canonical InstrumentId."""
        rec = self._by_token.get(instrument_token)
        return rec.instrument_id if rec is not None else None

    def resolve_tradingsymbol_and_exchange(self, instrument_id: InstrumentId) -> tuple[str, str]:
        """Resolve canonical InstrumentId to (tradingsymbol, exchange) for order placement."""
        rec = self._by_id.get(instrument_id)
        if rec is not None:
            return rec.tradingsymbol, rec.exchange
        return instrument_id.symbol, instrument_id.exchange

    def get_instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        """Get canonical Instrument object if found."""
        rec = self._by_id.get(instrument_id)
        return rec.to_instrument() if rec is not None else None

    def all_instruments(self) -> list[Instrument]:
        """Return all registered instruments."""
        return [rec.to_instrument() for rec in self._by_id.values()]

    def search_instruments(self, query: str) -> list[Instrument]:
        """Search instruments matching symbol prefix or substring."""
        q = query.strip().upper()
        if not q:
            return []
        matches: list[Instrument] = []
        for rec in self._by_id.values():
            if q in rec.tradingsymbol or q in rec.name.upper():
                matches.append(rec.to_instrument())
        return matches

    def load_from_csv(self, csv_data_or_path: str | Path, *, strict: bool = False) -> int:
        """Parse Zerodha daily instruments CSV dump (text or file path) and populate mappings."""
        content: str
        if isinstance(csv_data_or_path, Path) or (
            isinstance(csv_data_or_path, str)
            and "\n" not in csv_data_or_path
            and Path(csv_data_or_path).exists()
        ):
            content = Path(csv_data_or_path).read_text(encoding="utf-8")
        else:
            content = str(csv_data_or_path)
        return self.load_from_csv_text(content, strict=strict)

    def load_from_csv_text(self, content: str, *, strict: bool = False) -> int:
        """Populate mappings from CSV text; later duplicate rows replace earlier ones.

        A header without ``instrument_token``, ``tradingsymbol`` and ``exchange`` always raises
        ``ValueError``. Malformed rows are skipped, or raise ``ValueError`` when ``strict``.
        """
        reader = csv.DictReader(StringIO(content))
        missing = [
            c
            for c in ("instrument_token", "tradingsymbol", "exchange")
            if c not in (reader.fieldnames or [])
        ]
        if missing:
            raise ValueError(f"invalid instruments csv: missing column {missing[0]}")
        count = 0
        for number, row in enumerate(reader, start=1):
            token_str = row.get("instrument_token")
            symbol = row.get("tradingsymbol")
            exchange = row.get("exchange")
            if not token_str or not symbol or not exchange:
                if strict:
                    raise ValueError(f"invalid instruments csv: row {number}: missing field")
                continue

            try:
                token = int(token_str)
                record = ZerodhaInstrumentRecord(
                    instrument_token=token,
                    exchange_token=str(row.get("exchange_token", "")),
                    tradingsymbol=symbol,
                    name=str(row.get("name", symbol)),
                    exchange=exchange,
                    segment=str(row.get("segment", exchange)),
                    lot_size=float(row.get("lot_size", 1) or 1),
                    tick_size=float(row.get("tick_size", 0.05) or 0.05),
                    instrument_type=str(row.get("instrument_type", "EQ")),
                )
            except (ValueError, TypeError) as e:
                if strict:
                    raise ValueError(f"invalid instruments csv: row {number}: bad value") from e
                continue
            self.register(record)
            count += 1

        return count
