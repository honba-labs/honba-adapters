"""Pure tick -> bar aggregation (exchange-time buckets, closed bars only) and Bar wire JSON.

Rules:

* Buckets are computed from the tick's EXCHANGE timestamp, anchored at the 09:15 IST session
  open of that day (so 1m/5m/15m/25m/60m all line up with Dhan's own intraday candles).
* A bar is emitted exactly once and only after it has closed: either a tick from a later
  bucket proved it (exchange time), or ``drain(now)`` observed ``bucket_end + grace <= now``.
  The forming bar is never emitted. Minutes with no ticks produce no bar (no synthetic fill).
* The first (partial) bucket seen per instrument is dropped by default: the process started
  mid-bucket and its open/high/low/volume would be wrong.
* Volume is the delta of the cumulative day volume between ticks; if a feed mode carries no
  cumulative volume the last-trade quantity is summed instead.
* Ticks older than the bucket already being formed (late/out-of-order) are ignored.
* No clock is read here; callers pass ``now`` (injectable in tests).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from honba_dhan.timeframes import IST, timeframe_seconds

if TYPE_CHECKING:
    from honba.domain.instrument import InstrumentId

    from honba_dhan.websocket import FeedTick

__all__ = ["BarAggregator", "ClosedBar", "bar_start_ns", "bar_to_wire"]

NS = 1_000_000_000
_SESSION_OPEN_S = 9 * 3600 + 15 * 60  # 09:15 IST
_IST_OFFSET_S = int(IST.utcoffset(None).total_seconds())  # type: ignore[union-attr]


def bar_start_ns(ts_ns: int, interval_s: int) -> int:
    """Start (UTC ns) of the bucket containing ``ts_ns``, anchored at 09:15 IST each day."""
    local_s = ts_ns // NS + _IST_OFFSET_S
    day_start = local_s - local_s % 86400
    anchor = day_start + _SESSION_OPEN_S
    start_local = anchor + ((local_s - anchor) // interval_s) * interval_s
    return (start_local - _IST_OFFSET_S) * NS


@dataclass(frozen=True, slots=True)
class ClosedBar:
    instrument_id: InstrumentId
    start_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class _Forming:
    start_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    partial: bool


class BarAggregator:
    def __init__(
        self,
        interval: str = "1m",
        *,
        grace_seconds: float = 2.0,
        drop_first_partial: bool = True,
    ) -> None:
        self.interval = interval
        self.interval_s = timeframe_seconds(interval)
        if self.interval_s % 60 or self.interval_s >= 86400:
            raise ValueError(f"aggregation interval must be whole minutes under 1d: {interval!r}")
        self._grace_ns = int(grace_seconds * NS)
        self._drop_partial = drop_first_partial
        self._forming: dict[InstrumentId, _Forming] = {}
        self._last_cum: dict[InstrumentId, int] = {}
        self._ready: list[ClosedBar] = []
        self._seen: set[InstrumentId] = set()
        self.late_ticks = 0
        self.rejected_ticks = 0

    def on_tick(self, tick: FeedTick) -> None:
        price = tick.price
        if not math.isfinite(price) or price <= 0 or tick.ts <= 0:
            self.rejected_ticks += 1
            return
        inst = tick.instrument_id
        vol = self._volume_delta(inst, tick)
        start = bar_start_ns(tick.ts, self.interval_s)
        cur = self._forming.get(inst)

        if cur is not None and start < cur.start_ns:
            self.late_ticks += 1
            return
        if cur is not None and start > cur.start_ns:
            self._finish(inst, cur)
            cur = None
        if cur is None:
            # First bucket for this instrument is partial: we joined mid-bucket.
            first = inst not in self._seen
            self._seen.add(inst)
            self._forming[inst] = _Forming(start, price, price, price, price, vol, first)
            return
        cur.high = max(cur.high, price)
        cur.low = min(cur.low, price)
        cur.close = price
        cur.volume += vol

    def _volume_delta(self, inst: InstrumentId, tick: FeedTick) -> float:
        if tick.cum_volume is None:
            return float(max(tick.last_qty, 0))
        prev = self._last_cum.get(inst)
        self._last_cum[inst] = tick.cum_volume
        if prev is None:
            return 0.0  # no baseline yet
        if tick.cum_volume < prev:  # counter reset (new session): count from zero
            return float(tick.cum_volume)
        return float(tick.cum_volume - prev)

    def _finish(self, inst: InstrumentId, f: _Forming) -> None:
        del self._forming[inst]
        if f.partial and self._drop_partial:
            return
        self._ready.append(
            ClosedBar(inst, f.start_ns, f.open, f.high, f.low, f.close, max(f.volume, 0.0))
        )

    def drain(self, now_ns: int) -> list[ClosedBar]:
        """Closed bars not yet returned, ascending by start time. Never includes forming bars."""
        for inst, f in list(self._forming.items()):
            if f.start_ns + self.interval_s * NS + self._grace_ns <= now_ns:
                self._finish(inst, f)
        out = sorted(self._ready, key=lambda b: (b.start_ns, str(b.instrument_id)))
        self._ready = []
        return out


def _unix_nanos(ns: int) -> dict[str, str]:
    secs, rem = divmod(ns, NS)
    t = dt.datetime.fromtimestamp(secs, dt.timezone.utc)
    return {"iso": t.strftime("%Y-%m-%dT%H:%M:%S") + f".{rem:09d}Z", "unix_nanos": str(ns)}


def bar_to_wire(bar: ClosedBar, interval: str) -> dict[str, Any]:
    """Honba ``Bar`` JSON (same shape as honba-data's yfinance driver). Timestamps = bar START."""
    n, unit = int(interval[:-1]), interval[-1].lower()
    step, agg = (n, "hour") if unit == "h" else (n, "minute")
    t = _unix_nanos(bar.start_ns)
    return {
        "bar_type": {
            "instrument_id": {
                "symbol": bar.instrument_id.symbol,
                "exchange": bar.instrument_id.exchange,
            },
            "spec": {"step": step, "aggregation": agg, "price_type": "last"},
        },
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "ts_event": t,
        "ts_init": dict(t),
    }
