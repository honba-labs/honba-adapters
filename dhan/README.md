# honba-dhan

DhanHQ v2 adapter for Honba: execution, market data, streaming (`DhanAdapter`,
registered as `dhan`).

## Configuration

| Variable (prefix `DHAN_`) | Meaning |
|---|---|
| `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` | Credentials (required for live) |
| `DHAN_MODE` | `live` (default) or `paper` |
| `HONBA_CACHE_DIR` | Cache root; Dhan uses `$HONBA_CACHE_DIR/dhan` (default `~/.cache/honba/dhan`) |

```python
from honba_dhan import DhanAdapter, DhanConfig

DhanConfig.from_env()  # reads DHAN_*; from_env(prefix="ALT_") reads ALT_*
DhanAdapter()  # same env lookup; raises AuthenticationError if unset
DhanAdapter(mode="paper")  # no credentials needed; in-memory transport
DhanAdapter(DhanConfig.paper())
```

Bare construction never falls back to paper silently: without credentials in the
environment `DhanAdapter()` raises `AuthenticationError` naming `DHAN_CLIENT_ID` and
`DHAN_ACCESS_TOKEN`.

## Capabilities

| Dimension | Declared |
|---|---|
| Exchanges | `NSE`, `BSE`, `MCX` |
| Products | `delivery`, `intraday`, `carry` |
| Order types | `market`, `limit`, `stop_limit`, `stop_market` |
| Time in force | `day`, `ioc` |
| Price types | `bid`, `ask`, `last`, `mid` |
| Stream modes | `ltp`, `quote`, `depth` |
| Features | all 14: place/modify/cancel/cancel-all, order and trade book, positions, holdings, funds, margin, quotes, depth, historical bars, instrument master |

`trailing_stop`, `gtc`, `fok` and `gtd` are not declared. Unsupported products, order types
and time in force raise `CapabilityError` before any network call. Wire mappings live in
`constants.py` as `WireMap`s (`DHAN_*_WIRE`); `assert_covers` checks them against these
capabilities at import.

## Tests and certification

From the repository root:

```sh
python -m pytest dhan/tests                       # offline unit tests
python -m pytest tests/test_contract_suite.py -k dhan   # certification on recorded fixtures
```

`test_contract_suite[dhan]` is driven by `tests/fixtures/dhan/routes.toml`.

### Live tests (read-only)

They connect, check funds, and read positions, holdings, orders and trades. They skip unless
both variables are set, and a conftest guard refuses any non-read request.

```fish
set -x DHAN_CLIENT_ID <id>
set -x DHAN_ACCESS_TOKEN <token>
cd ~/Devel/Finance/honba-labs/honba-adapters
set -x PYTHONPATH $PWD/dhan/src:$PWD/shared/src
python -m pytest -m live dhan/tests/live -v
```
