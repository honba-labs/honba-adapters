"""BarAggregator: exchange-time IST bucketing, closed-bars-only, volume deltas (pure, no I/O)."""

from __future__ import annotations

import datetime as dt

import pytest
from honba.domain.instrument import InstrumentId

from honba_adapters.dhan.aggregator import NS, BarAggregator, bar_start_ns, bar_to_wire
from honba_adapters.dhan.websocket import FeedTick

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
RIL = InstrumentId("RELIANCE", "NSE")
TCS = InstrumentId("TCS", "NSE")


def ns(h: int, m: int, s: float = 0.0, day: int = 2) -> int:
    t = dt.datetime(2024, 1, day, h, m, tzinfo=IST)
    return int(t.timestamp()) * NS + int(s * NS)


def tick(
    inst: InstrumentId, t: int, price: float, cum: int | None = None, qty: int = 0
) -> FeedTick:
    return FeedTick(inst, t, price, cum, qty)


def agg(interval: str = "1m", **kw: object) -> BarAggregator:
    return BarAggregator(interval, **kw)  # type: ignore[arg-type]


def warm(a: BarAggregator, inst: InstrumentId = RIL, at: int = ns(9, 15), cum: int = 1000) -> None:
    """Consume the (dropped) first partial bucket so later bars are emitted."""
    a.on_tick(tick(inst, at, 100.0, cum))


class TestBucketing:
    def test_session_anchor_and_alignment(self) -> None:
        assert bar_start_ns(ns(9, 15, 30), 60) == ns(9, 15)
        assert bar_start_ns(ns(9, 16, 59.9), 60) == ns(9, 16)
        assert bar_start_ns(ns(9, 19, 59), 300) == ns(9, 15)
        assert bar_start_ns(ns(9, 20), 300) == ns(9, 20)
        assert bar_start_ns(ns(10, 14, 59), 3600) == ns(9, 15)
        assert bar_start_ns(ns(10, 15), 3600) == ns(10, 15)
        assert bar_start_ns(ns(9, 39, 59), 1500) == ns(9, 15)
        assert bar_start_ns(ns(9, 40), 1500) == ns(9, 40)

    def test_buckets_use_ist_not_utc(self) -> None:
        # 03:45:30 UTC == 09:15:30 IST
        t = int(dt.datetime(2024, 1, 2, 3, 45, 30, tzinfo=dt.timezone.utc).timestamp()) * NS
        assert bar_start_ns(t, 60) == ns(9, 15)

    def test_day_boundary_resets_anchor(self) -> None:
        assert bar_start_ns(ns(9, 15, day=3), 60) == ns(9, 15, day=3)

    @pytest.mark.parametrize("bad", ["0m", "1d", "90s", "x", "2h30m"])
    def test_invalid_intervals(self, bad: str) -> None:
        with pytest.raises(ValueError):
            BarAggregator(bad)


class TestEmission:
    def test_ohlc_and_emit_on_next_bucket_tick(self) -> None:
        a = agg()
        warm(a)
        for s, p in [(5, 101.0), (20, 105.0), (40, 99.0), (59, 102.0)]:
            a.on_tick(tick(RIL, ns(9, 16, s), p, 1000))
        assert a.drain(ns(9, 16, 59)) == []  # still forming
        a.on_tick(tick(RIL, ns(9, 17, 1), 103.0, 1000))  # proves 09:16 closed
        [bar] = a.drain(ns(9, 17, 1))
        assert (bar.start_ns, bar.open, bar.high, bar.low, bar.close) == (
            ns(9, 16),
            101.0,
            105.0,
            99.0,
            102.0,
        )

    def test_forming_bar_never_emitted_and_clock_close_with_grace(self) -> None:
        a = agg(grace_seconds=2)
        warm(a)
        a.on_tick(tick(RIL, ns(9, 16, 10), 101.0, 1000))
        assert a.drain(ns(9, 17, 1.9)) == []
        [bar] = a.drain(ns(9, 17, 2))
        assert bar.start_ns == ns(9, 16)
        assert a.drain(ns(9, 30)) == []  # emitted once only

    def test_each_bar_emitted_exactly_once(self) -> None:
        a = agg()
        warm(a)
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 1000))
        a.on_tick(tick(RIL, ns(9, 17, 1), 102.0, 1000))
        assert len(a.drain(ns(9, 17, 1))) == 1
        assert a.drain(ns(9, 17, 1)) == []

    def test_gap_minutes_produce_no_bars(self) -> None:
        a = agg()
        warm(a)
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 1000))
        a.on_tick(tick(RIL, ns(9, 20, 1), 102.0, 1000))
        assert [b.start_ns for b in a.drain(ns(9, 20, 1))] == [ns(9, 16)]

    def test_first_partial_bucket_dropped_by_default(self) -> None:
        a = agg()
        a.on_tick(tick(RIL, ns(9, 15, 40), 100.0, 1000))
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 1000))
        assert a.drain(ns(9, 16, 1)) == []

    def test_first_partial_kept_when_configured(self) -> None:
        a = agg(drop_first_partial=False)
        a.on_tick(tick(RIL, ns(9, 15, 40), 100.0, 1000))
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 1000))
        assert len(a.drain(ns(9, 16, 1))) == 1

    def test_multiple_instruments_sorted_ascending(self) -> None:
        a = agg()
        warm(a, RIL)
        warm(a, TCS)
        a.on_tick(tick(TCS, ns(9, 16, 5), 50.0, 1000))
        a.on_tick(tick(RIL, ns(9, 17, 5), 101.0, 1000))
        a.on_tick(tick(RIL, ns(9, 18, 5), 101.0, 1000))
        a.on_tick(tick(TCS, ns(9, 18, 6), 51.0, 1000))
        bars = a.drain(ns(9, 18, 6))
        assert [(b.start_ns, b.instrument_id.symbol) for b in bars] == [
            (ns(9, 16), "TCS"),
            (ns(9, 17), "RELIANCE"),
        ]

    def test_late_ticks_ignored(self) -> None:
        a = agg()
        warm(a)
        a.on_tick(tick(RIL, ns(9, 17, 5), 101.0, 1000))
        a.on_tick(tick(RIL, ns(9, 16, 59), 500.0, 1000))
        assert a.late_ticks == 1
        a.on_tick(tick(RIL, ns(9, 18, 5), 102.0, 1000))
        assert [b.high for b in a.drain(ns(9, 18, 5))] == [101.0]

    @pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
    def test_bad_prices_rejected(self, price: float) -> None:
        a = agg()
        a.on_tick(tick(RIL, ns(9, 16), price, 1))
        assert a.rejected_ticks == 1

    def test_five_minute_interval(self) -> None:
        a = agg("5m")
        warm(a)
        for m in (20, 22, 24):
            a.on_tick(tick(RIL, ns(9, m, 1), 100.0 + m, 1000))
        a.on_tick(tick(RIL, ns(9, 25, 0), 1.0, 1000))
        [bar] = a.drain(ns(9, 25, 0))
        assert bar.start_ns == ns(9, 20)
        assert bar.close == 124.0 and bar.open == 120.0


class TestVolume:
    def test_delta_of_cumulative_volume(self) -> None:
        a = agg()
        warm(a, cum=1000)
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 1100))
        a.on_tick(tick(RIL, ns(9, 16, 30), 101.0, 1350))
        a.on_tick(tick(RIL, ns(9, 17, 1), 101.0, 1400))
        [bar] = a.drain(ns(9, 17, 1))
        assert bar.volume == 350.0  # 1100-1000 + 1350-1100

    def test_cumulative_reset_counts_from_zero(self) -> None:
        a = agg()
        warm(a, cum=5000)
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 40))
        a.on_tick(tick(RIL, ns(9, 17, 1), 101.0, 40))
        [bar] = a.drain(ns(9, 17, 1))
        assert bar.volume == 40.0

    def test_no_cumulative_falls_back_to_last_qty(self) -> None:
        a = agg()
        a.on_tick(tick(RIL, ns(9, 15, 1), 100.0, None, 5))
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, None, 7))
        a.on_tick(tick(RIL, ns(9, 16, 2), 101.0, None, 3))
        a.on_tick(tick(RIL, ns(9, 17, 1), 101.0, None, 1))
        [bar] = a.drain(ns(9, 17, 1))
        assert bar.volume == 10.0

    def test_volume_never_negative(self) -> None:
        a = agg()
        warm(a)
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 1000))
        a.on_tick(tick(RIL, ns(9, 17, 1), 101.0, 1000))
        [bar] = a.drain(ns(9, 17, 1))
        assert bar.volume == 0.0


class TestWire:
    def test_wire_shape_matches_yfinance_driver(self) -> None:
        a = agg()
        warm(a)
        a.on_tick(tick(RIL, ns(9, 16, 1), 101.0, 1010))
        a.on_tick(tick(RIL, ns(9, 17, 1), 101.0, 1010))
        [bar] = a.drain(ns(9, 17, 1))
        w = bar_to_wire(bar, "1m")
        assert w["bar_type"] == {
            "instrument_id": {"symbol": "RELIANCE", "exchange": "NSE"},
            "spec": {"step": 1, "aggregation": "minute", "price_type": "last"},
        }
        # 2024-01-02 09:16 IST == 03:46 UTC
        assert w["ts_event"] == {
            "iso": "2024-01-02T03:46:00.000000000Z",
            "unix_nanos": str(ns(9, 16)),
        }
        assert w["ts_init"] == w["ts_event"]
        assert (w["open"], w["volume"]) == (101.0, 10.0)

    def test_hour_and_multi_minute_specs(self) -> None:
        a = agg("5m")
        warm(a)
        a.on_tick(tick(RIL, ns(9, 20, 1), 1.0, 1000))
        a.on_tick(tick(RIL, ns(9, 25, 1), 1.0, 1000))
        [bar] = a.drain(ns(9, 25, 1))
        assert bar_to_wire(bar, "5m")["bar_type"]["spec"]["step"] == 5
        assert bar_to_wire(bar, "1h")["bar_type"]["spec"] == {
            "step": 1,
            "aggregation": "hour",
            "price_type": "last",
        }
