# honba-adapters

Indian broker adapters for [Honba](../honba), the AI-native trading and research
platform.

Each broker is an independent Python package in this repository. A package implements
the core adapter contract from `honba.adapters` — a lifecycle facade plus a market-data
role and an execution role ([ADR 0010](../honba/docs/adr/0010-adapter-contract.md)) — and
advertises itself through the `honba.adapters` entry-point group. The engine discovers an
adapter by name and never imports a broker SDK itself.

## Adapters

| Package | Broker | Auth | Status |
|---|---|---|---|
| [`honba-dhan`](dhan) | DhanHQ | Access token | Implemented: execution, market data, streaming, contract-certified |
| [`honba-zerodha`](zerodha) | Zerodha Kite Connect v3 | OAuth (daily) | Implemented: execution, market data, KiteTicker, contract-certified |
| `honba-angelone` | Angel One SmartAPI | TOTP + API key | Package scaffold only |
| `honba-fyers` | Fyers API v3 | OAuth | Package scaffold only |
| `honba-upstox` | Upstox Uplink | OAuth (daily) | Package scaffold only |
| `honba-kotak` | Kotak Neo | OAuth | Package scaffold only |
| `honba-iifl` | IIFL Markets | — | Package scaffold only |
| `honba-motilal` | Motilal Oswal | — | Package scaffold only |
| [`honba-adapters-shared`](shared) | — | — | Shared infrastructure for every adapter above |

Only `dhan` and `zerodha` are registered through the `honba.adapters` entry point today.
The remaining packages have the uniform layout in place but no implementation yet.

## Repository layout

```
honba-adapters/
├── shared/                  honba-adapters-shared: contract suite, boundary check,
│                            fixtures, rate limiting, TOTP auth, websocket transport
├── dhan/                    honba-dhan:    src/honba_dhan/, tests/
├── zerodha/                 honba-zerodha: src/honba_zerodha/, tests/
├── angelone/ … motilal/     uniform package skeletons (src/honba_<broker>/)
├── configs/                 paper/live TOML for dhan and zerodha
├── examples/                runnable adapter examples
└── scripts/smoke_test.py    guarded live/paper smoke test runner
```

Every adapter follows the same internal structure: `config.py` (config dataclass),
`data.py` (market-data role), `execution.py` (execution role), `instruments.py`,
`websocket.py`, `http.py`, `parsing.py` and `constants.py`. The facade and both roles are
satisfied by one object, e.g. `honba_dhan.DhanAdapter`.

## The adapter contract

The contract lives in the core, not here:

- `Adapter` — identity (`name`), `capabilities()`, `connect()`, `disconnect()`,
  `session()`, `is_connected()`, plus the `require_connected()` /
  `require_capabilities()` guards.
- `MarketDataAdapter` — `instruments`, `search_instruments`, `quote`, `depth`,
  `historical_bars`, `subscribe`, `unsubscribe`.
- `ExecutionAdapter` — `place_order`, `modify_order`, `cancel_order`, `cancel_all`,
  `order_status`, `orders`, `trades`, `positions`, `holdings`, `funds`, `margin`.

Two rules matter most: an unsupported request raises `CapabilityError` **before** any
network call, and a broker refusal comes back as an `OrderReport` with
`status=REJECTED`, never as an exception. See
[Adapter Interface](../honba-docs/book/src/architecture/adapter_interface.md) for the
full surface and value types.

`honba-adapters-shared` packages the pieces every adapter reuses:

- `assert_adapter_contract(adapter)` — runs the behavioural contract suite
  (`honba.adapters.contract`) and raises `ContractViolation` on the first broken rule.
- `find_boundary_violations` — keeps broker wire types inside their adapter.
- `load_fixture` / `fixtures_dir` — read recorded broker responses from `tests/fixtures/`.
- `RateLimiter` / `TokenBucket` / `rate_limited` — token-bucket rate limiting with an
  injectable clock.
- `TOTP` / `TokenManager` / `FileTokenStore` — RFC 6238 TOTP and token management.
- `WireMap` / `assert_covers` — one-dict honba-to-wire mappings (reverse derived, duplicates
  rejected) and a check that every declared capability has a mapping.
- `ReconnectingWebSocketClient` / `SequenceTracker` — reconnecting transport with
  gap detection.

## Install

The repo is a collection of editable packages; install them into a virtualenv alongside
the core `honba` package from the sibling checkout.

```sh
cd honba-adapters
python -m venv .venv && source .venv/bin/activate

pip install -e ../honba/python          # core: honba.adapters.*
pip install -e "shared[dev]"            # contract suite + dev deps
pip install -e dhan -e zerodha          # the implemented adapters
```

## Configuration

Credentials come from the environment, one prefix per adapter (`DHAN_`, `ZERODHA_`).
`<PREFIX>MODE` is `live` (the default) or `paper`.

| Adapter | Variables (`<PREFIX>` = `DHAN_` / `ZERODHA_`) |
|---|---|
| dhan | `<PREFIX>CLIENT_ID`, `<PREFIX>ACCESS_TOKEN`, `<PREFIX>MODE` |
| zerodha | `<PREFIX>API_KEY`, `<PREFIX>ACCESS_TOKEN`, `<PREFIX>API_SECRET` (optional), `<PREFIX>USER_ID` (optional), `<PREFIX>MODE` |

- `XConfig.from_env(prefix="DHAN_")` reads that set; pass another prefix to run several
  accounts side by side.
- **Credentials are required.** `DhanAdapter()` / `ZerodhaAdapter()` with no config and no
  credentials in the environment raises `AuthenticationError` naming the missing variables.
  For simulation ask for it explicitly: `mode="paper"`, `XConfig.paper()` or
  `<PREFIX>MODE=paper` (paper mode needs no credentials and uses an in-memory transport).
- Declared capabilities (exchanges, products, order types, time in force, features) are
  tabulated in each adapter's README. `OrderType.TRAILING_STOP` is opt-in and no adapter
  lists it today; see [Trailing stop support](#trailing-stop-support).
- `HONBA_CACHE_DIR` moves the cache root; each adapter uses `$HONBA_CACHE_DIR/<broker>`
  (default `~/.cache/honba/<broker>`).

## Trailing stop support

`OrderType.TRAILING_STOP` (with `trail_amount` or `trail_percent`) is only declared where a
broker documents a native, server-side trailing stop for a standalone order, with an
unambiguous mapping of the trail distance. Reviewed against published docs (Oct 2026):

| Adapter | Native feature | Source | Decision | Reason |
|---|---|---|---|---|
| dhan | Super Order `trailingJump` (absolute price jump) | https://dhanhq.co/docs/v2/super-order/ | excluded | Only inside a Super Order (entry + mandatory target + SL legs), not a standalone trailing stop; docs do not state the trail is server-side; no tick/min rules; absolute only (no percent) |
| zerodha | Trailing SL on GTT, Kite Web only | https://support.zerodha.com/category/trading-and-markets/charts-and-orders/gtt/articles/trailing-stop-loss | excluded | Not exposed in Kite Connect API (announced as future) |
| angelone | `trailingStopLoss` on ROBO (bracket) orders | https://smartapi.angelone.in/topic/4547/robo-trailing-stop-loss-not-being-honored | excluded | Bracket-only (needs squareoff + stoploss); developers report it is not honoured; units undocumented |
| fyers | Smart Trail TSL on platform | https://support.fyers.in/portal/en/kb/articles/how-do-i-place-a-trailing-stop-loss-smart-trail-order-in-fyers | excluded | No trailing parameter in the published API v3 order parameters (https://api-connect-docs.fyers.in/docs/parameters) |
| upstox | GTT v3 `trailing_gap` on stoploss rule | https://community.upstox.com/t/api-gtt-trailing-stop-loss-order-issue/13080 | excluded | GTT, not a regular order; units undocumented; users report it behaves as a plain stop loss; adapter has no GTT path |
| kotak | Trailing SL on Bracket orders (platform) | https://www.kotakneo.com/support/how-can-i-set-a-trailing-stoploss/ | excluded | Bracket-only; no API parameter documented |
| iifl | none found | - | excluded | No trailing stop documented in the public API |
| motilal | none found | - | excluded | No trailing stop documented in the public API (advanced orders are strategy-layer) |

Excluded adapters refuse `TRAILING_STOP` through pre-flight capability validation. Revisit
when a broker documents a standalone server-side trailing order.

## Usage

Paper mode needs no credentials: the adapter installs an in-memory mock transport.

```python
import asyncio

from honba.adapters import available_adapters, resolve_adapter
from honba.domain.instrument import InstrumentId


async def main() -> None:
    print(available_adapters())  # ['dhan', 'zerodha', ...]

    adapter = resolve_adapter("zerodha", mode="paper")  # or mode="live" with credentials
    await adapter.connect()
    try:
        quote = await adapter.quote(InstrumentId("RELIANCE", "NSE"))
        print(quote.bid_price, quote.ask_price)
    finally:
        await adapter.disconnect()


asyncio.run(main())
```

A run config selects the adapter by name, and the engine resolves it with
`resolve_adapter(name, **config)`; `configs/` holds the paper and live TOML templates:

```toml
[adapter]
name = "dhan"
mode = "paper"
client_id = "${DHAN_CLIENT_ID}"
access_token = "${DHAN_ACCESS_TOKEN}"
```

## Tests

The default run is deterministic and offline.

```sh
python -m pytest            # shared + dhan + zerodha (pytest.ini testpaths)
python -m pytest shared/tests
python -m pytest dhan/tests zerodha/tests
```

Dhan additionally has live, read-only smoke tests. They are marked `live` and skip
themselves unless both environment variables are set; a conftest guard refuses any
request that is not a documented read endpoint, so they cannot place an order.

```sh
export DHAN_CLIENT_ID=... DHAN_ACCESS_TOKEN=...
python -m pytest -m live dhan/tests/live -v
```

Lint with `ruff check .` and `ruff format --check .` (line length 100).

### Certification (contract suite)

An adapter is certified when `test_contract_suite[<name>]` passes:

```sh
python -m pytest tests/test_contract_suite.py -v
```

The suite runs `honba.adapters.contract.verify_adapter_contract` over every package that has
`tests/fixtures/<name>/routes.toml`. Read endpoints are replayed from recorded responses in
that directory; the stateful order lifecycle uses the adapter's in-memory mock handler. Packages
without a `routes.toml` (the six scaffolds) are discovered automatically and reported as
skipped placeholders. Needs Python 3.11+ (`tomllib`).

## Smoke test runner

`scripts/smoke_test.py` drives a full connect → funds → books → quote/depth → order
lifecycle → disconnect against an adapter. It defaults to paper and refuses live mode
unless `HONBA_LIVE_SMOKE_TEST=1` is set (and even then places no order unless
`--place-live-order` is passed).

```sh
python scripts/smoke_test.py --adapter dhan --mode paper
HONBA_LIVE_SMOKE_TEST=1 python scripts/smoke_test.py --adapter dhan --mode live \
  --client-id "$DHAN_CLIENT_ID" --access-token "$DHAN_ACCESS_TOKEN"
```

## Examples

[`examples/`](examples) contains runnable, registry-driven scripts:

- `01_connect_dhan.py` — connect through the registry and print session + capabilities.
- `04_place_order_paper.py` — place, repeat (idempotent), rest/cancel and reject orders,
  then read the books back.

Both default to the in-memory `fake` adapter, so they run with no credentials. Pass
`--adapter dhan` or `--adapter zerodha` to drive a real package.

## Broker notes

**Zerodha.** Time in force is `DAY` and `IOC` only — `GTC`, `FOK` and `GTD` are rejected
with `CapabilityError` before any network call, as are fractional quantities. Order tags
are ASCII alphanumeric, at most 20 characters. Ambiguous submits are reconciled by tag on
the next call. Full behaviour is in
[`honba-docs/.../india/brokers.md`](../honba-docs/docs/india/brokers.md).

## Adding a broker

Start from `dhan/` as the template, then:

1. Implement the facade and both roles in `<name>/src/honba_<name>/`, declaring exactly what
   the broker supports in `AdapterCapabilities`.
2. Build the honba-to-wire tables with `honba_adapters_shared.WireMap` and call
   `assert_covers(capabilities, order_types=..., products=..., tif=...)` at import, so a
   declared capability without a mapping fails immediately instead of at order time.
3. Declare the entry point in `<name>/pyproject.toml`:
   ```toml
   [project.entry-points."honba.adapters"]
   mybroker = "honba_mybroker:MyBrokerAdapter"
   ```
4. Record read responses in `tests/fixtures/<name>/` and add `routes.toml` there (adapter,
   config and fallback `module:attr` hooks plus the route-to-file map). The contract suite picks
   the package up with no edit to `tests/test_contract_suite.py`.
5. Add the package to `pytest.ini` (`pythonpath`, `testpaths`), `.github/workflows/ci.yml`
   (`pip install -e`) and the install line above.

Details are in [MIGRATION.md](MIGRATION.md) and
[Adding Broker Adapters](../honba-docs/book/src/contributing/adding_adapters.md).

## Testing and the honba dependency

Every package depends on bare `honba`, which is not on PyPI and needs its Rust
extension built with maturin. CI (`.github/workflows/ci.yml`) therefore checks out
`honba-labs/honba` at the ref in the workflow's `HONBA_REF` env var (currently `main`,
so the adapters are tested against current core), runs `maturin develop`, installs
`shared`, `dhan` and `zerodha` editable, then runs ruff and the full pytest suite from
the repo root (shared, the root contract suite, dhan, zerodha). Live broker tests
skip unless credentials are set. If the adapter contract in `honba` changes
incompatibly, pin `HONBA_REF` to a tag or SHA until the adapters catch up.

Locally, with `honba` installed in a venv: `pip install pytest pytest-asyncio duckdb
yfinance cryptography && pip install -e shared -e dhan -e zerodha && pytest -q`.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
