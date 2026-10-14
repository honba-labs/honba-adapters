"""Dhan live feed -> Honba ``POST /bars`` driver.

Streams ticks from the Dhan market feed, aggregates them into CLOSED bars (see
:mod:`honba_dhan.aggregator`) and posts them to ``{server}/bars`` with the same conventions as
honba-data's yfinance driver: a per-instrument high-water mark (each bar posted once, ascending),
batches of at most 1000 bars, 429 (``bar_ingest_full``) exponential backoff, and 503
(``bar_ingest_disabled``) / any other rejection fatal.

All I/O is injectable (``http_post``, ``clock``, ``sleep``) so tests need no network.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from honba.adapters.models import StreamMode
from honba.domain.instrument import InstrumentId

from honba_dhan.aggregator import BarAggregator, ClosedBar, bar_to_wire
from honba_dhan.constants import EXCHANGE_TO_SEGMENT

if TYPE_CHECKING:
    from honba_dhan import DhanAdapter

logger = logging.getLogger(__name__)

__all__ = ["DhanBarDriver", "IngestFatalError", "default_http_post", "parse_symbol_spec"]

MAX_BATCH = 1000
HttpPostFn = Callable[[str, str, dict[str, Any]], tuple[int, dict[str, Any]]]


class IngestFatalError(RuntimeError):
    """The server refused ingestion in a way retrying will not fix."""


def parse_symbol_spec(spec: str) -> tuple[InstrumentId, str | None]:
    """``RELIANCE``, ``RELIANCE.NSE`` or ``RELIANCE.NSE=1333`` -> (id, security id or None)."""
    spec = spec.strip()
    sec_id: str | None = None
    if "=" in spec:
        spec, sec_id = (x.strip() for x in spec.split("=", 1))
        if not sec_id.isdigit():
            raise ValueError(f"security id must be numeric in {spec!r}={sec_id!r}")
    symbol, exchange = spec, "NSE"
    if "." in spec:
        sym, exch = spec.rsplit(".", 1)
        if exch.upper() in {"NSE", "BSE"}:
            symbol, exchange = sym, exch.upper()
    if not symbol:
        raise ValueError("empty symbol")
    return InstrumentId(symbol.upper(), exchange), sec_id


def default_http_post(url: str, token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        return exc.code, body


def _reason(body: dict[str, Any]) -> str:
    err = body.get("error") or {}
    return str((err.get("context") or {}).get("reason") or err.get("code") or "")


class DhanBarDriver:
    """Stream Dhan ticks -> closed bars -> Honba ``POST /bars``."""

    def __init__(
        self,
        adapter: DhanAdapter,
        symbols: Sequence[str],
        interval: str,
        server: str,
        token: str,
        *,
        http_post: HttpPostFn = default_http_post,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_seconds: float = 1.0,
        backoff_seconds: float = 1.0,
        max_retries: int = 5,
        grace_seconds: float = 2.0,
        backfill: bool = False,
    ) -> None:
        self._adapter = adapter
        self.interval = interval
        self.aggregator = BarAggregator(interval, grace_seconds=grace_seconds)
        self.url = server.rstrip("/") + "/bars"
        self._token = token
        self._post = http_post
        self._clock = clock
        self._sleep = sleep
        self.poll_seconds = poll_seconds
        self.backoff_seconds = backoff_seconds
        self.max_retries = max_retries
        self.backfill = backfill
        self._stop = asyncio.Event()
        self._watermark: dict[InstrumentId, int] = {}
        self._pending: list[tuple[int, ClosedBar]] = []

        self.targets: list[InstrumentId] = []
        for spec in symbols:
            inst, sec_id = parse_symbol_spec(spec)
            if sec_id is not None:
                seg = EXCHANGE_TO_SEGMENT[(inst.exchange, "EQUITY")]
                adapter._instruments.add_instrument(  # pyright: ignore[reportPrivateUsage]
                    security_id=sec_id, exchange_segment=seg, symbol=inst.symbol
                )
            if adapter._instruments.resolve_to_dhan(inst) is None:  # pyright: ignore[reportPrivateUsage]
                raise ValueError(
                    f"unknown instrument {inst.symbol}.{inst.exchange}: pass "
                    f"{inst.symbol}.{inst.exchange}=<Dhan security id>"
                )
            self.targets.append(inst)

    def __repr__(self) -> str:
        return (
            f"DhanBarDriver(targets={self.targets!r}, interval={self.interval!r}, url={self.url!r})"
        )

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ posting

    async def _send_async(self, batch: list[dict[str, Any]]) -> bool:
        """POST one batch off-thread. True if accepted, False if gave up on 429; raises if fatal."""
        for attempt in range(self.max_retries + 1):
            status, body = await asyncio.to_thread(
                self._post, self.url, self._token, {"bars": batch}
            )
            if status == 200:
                return True
            if status == 429:
                if attempt < self.max_retries:
                    delay = self.backoff_seconds * (2**attempt)
                    logger.warning("server busy (%s); retrying in %.1fs", _reason(body), delay)
                    await self._sleep(delay)
                continue
            if status == 503:
                raise IngestFatalError(
                    f"server answered 503 ({_reason(body) or 'market_data_unavailable'}): "
                    "bar ingest is disabled; start it with `honba serve --bar-ingest`"
                )
            raise IngestFatalError(f"server rejected batch with HTTP {status}: {_reason(body)}")
        logger.warning(
            "server still busy after %d retries; will retry next cycle", self.max_retries
        )
        return False

    def _enqueue(self, bars: Sequence[ClosedBar]) -> None:
        for b in bars:
            if b.start_ns > self._watermark.get(b.instrument_id, -1):
                self._pending.append((b.start_ns, b))
        self._pending.sort(key=lambda p: (p[0], str(p[1].instrument_id)))

    async def _flush(self) -> int:
        accepted = 0
        while self._pending:
            chunk = self._pending[:MAX_BATCH]
            wire = [bar_to_wire(b, self.interval) for _, b in chunk]
            if not await self._send_async(wire):
                break
            for _, b in chunk:
                prev = self._watermark.get(b.instrument_id, -1)
                self._watermark[b.instrument_id] = max(prev, b.start_ns)
            del self._pending[: len(chunk)]
            accepted += len(chunk)
        if accepted:
            logger.info("posted %d bars", accepted)
        return accepted

    async def step(self) -> int:
        """Drain closed bars (by the injected clock) and post them. Returns bars accepted."""
        now_ns = int(self._clock().timestamp() * 1_000_000_000)
        self._enqueue(self.aggregator.drain(now_ns))
        return await self._flush()

    # ------------------------------------------------------------------ lifecycle

    async def _do_backfill(self) -> None:
        """Post today's already-closed bars from REST history (watermark dedupes vs live)."""
        now = self._clock()
        today = now.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).replace(
            hour=9, minute=15, second=0, microsecond=0
        )
        for inst in self.targets:
            rows = await self._adapter.historical_bars(
                inst, timeframe=self.interval, start=today, end=now
            )
            self._enqueue(
                [ClosedBar(inst, r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows]
            )
        await self._flush()

    async def run(self) -> None:
        """Connect, stream and post until :meth:`stop` or a fatal ingest error."""
        await self._adapter.connect()
        try:
            feed = self._adapter.feed
            if feed is None:
                raise RuntimeError("Dhan live feed is unavailable (paper mode or no feed)")
            feed.add_tick_listener(self.aggregator.on_tick)
            if self.backfill:
                await self._do_backfill()
            await self._adapter.subscribe(
                tuple(self.targets), mode=StreamMode.QUOTE, callback=lambda _e: None
            )
            while not self._stop.is_set():
                await self.step()
                if self._stop.is_set():
                    break
                await self._sleep(self.poll_seconds)
        finally:
            await self._adapter.disconnect()
