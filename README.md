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

Lint with `ruff check .` (line length 100).

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

## Adding an adapter

Start from the Dhan package as the template, implement the facade and both role
protocols, add fixtures and tests, run `assert_adapter_contract`, and declare the entry
point:

```toml
[project.entry-points."honba.adapters"]
mybroker = "honba_mybroker:MyBrokerAdapter"
```

The step-by-step guide is
[Adding Broker Adapters](../honba-docs/book/src/contributing/adding_adapters.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
