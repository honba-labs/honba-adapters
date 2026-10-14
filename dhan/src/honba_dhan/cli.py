"""``honba-dhan`` command line: stream Dhan live bars into a Honba server.

    honba-dhan live --symbols RELIANCE,TCS --interval 1m \\
        --server http://127.0.0.1:8080 --token-env HONBA_TOKEN

Credentials come from DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN (never argv); the Honba bearer token
is read from the environment variable named by ``--token-env``. The server must run with
``honba serve --bar-ingest``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Sequence

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="honba-dhan")
    sub = p.add_subparsers(dest="command", required=True)
    live = sub.add_parser("live", help="stream closed bars from the Dhan live feed to POST /bars")
    live.add_argument(
        "--symbols",
        required=True,
        help="comma list: RELIANCE, TCS.NSE, or SYMBOL.EXCH=<Dhan security id> for unseeded ones",
    )
    live.add_argument("--interval", default="1m", help="1m (default), 5m, 15m, 25m, 60m")
    live.add_argument("--server", default="http://127.0.0.1:8080", help="Honba server base URL")
    live.add_argument("--token-env", default="HONBA_TOKEN", help="env var holding the Honba token")
    live.add_argument("--backfill", action="store_true", help="also post today's closed bars first")
    live.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"error: set {args.token_env} to the Honba bearer token", file=sys.stderr)
        return 2

    from honba_dhan import DhanAdapter
    from honba_dhan.driver import DhanBarDriver, IngestFatalError

    try:
        adapter = DhanAdapter()  # reads DHAN_* from the environment, or fails loudly
        driver = DhanBarDriver(
            adapter,
            [s for s in args.symbols.split(",") if s.strip()],
            args.interval,
            args.server,
            token,
            backfill=args.backfill,
        )
    except Exception as exc:  # noqa: BLE001  config/credential/symbol errors: message only, no tracebacks
        print(f"error: {exc}", file=sys.stderr)
        return 2

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, driver.stop)
        await driver.run()

    try:
        asyncio.run(_run())
    except IngestFatalError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
