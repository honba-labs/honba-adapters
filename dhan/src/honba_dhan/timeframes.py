"""Timeframe helpers shared by historical fetching, tick aggregation and the live driver."""

from __future__ import annotations

import datetime as dt
import re

__all__ = ["DHAN_INTRADAY_INTERVALS", "IST", "UTC", "timeframe_seconds", "to_dhan_interval"]

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")

# Minute intervals Dhan's /charts/intraday accepts.
DHAN_INTRADAY_INTERVALS: frozenset[int] = frozenset({1, 5, 15, 25, 60})

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def timeframe_seconds(timeframe: str) -> int:
    """``1m`` -> 60, ``1h`` -> 3600, ``1d`` -> 86400. Raises ``ValueError`` otherwise."""
    m = re.fullmatch(r"(\d+)([mhd])", timeframe.strip().lower())
    if not m or int(m.group(1)) < 1:
        raise ValueError(f"unsupported timeframe {timeframe!r}; use e.g. 1m, 5m, 15m, 1h, 1d")
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


def to_dhan_interval(timeframe: str) -> str:
    """Intraday timeframe -> Dhan ``interval`` string ("1", "5", "15", "25", "60")."""
    minutes, rem = divmod(timeframe_seconds(timeframe), 60)
    if rem or minutes not in DHAN_INTRADAY_INTERVALS or timeframe.strip().lower().endswith("d"):
        raise ValueError(f"Dhan intraday supports 1m, 5m, 15m, 25m, 60m/1h; got {timeframe!r}")
    return str(minutes)
