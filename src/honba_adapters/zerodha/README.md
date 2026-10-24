# honba-zerodha

Zerodha Kite Connect v3 adapter for Honba: execution, market data, KiteTicker streaming
(`ZerodhaAdapter`, registered as `zerodha`).

## Configuration

| Variable (prefix `ZERODHA_`) | Meaning |
|---|---|
| `ZERODHA_API_KEY`, `ZERODHA_ACCESS_TOKEN` | Credentials (required for live; the access token is daily) |
| `ZERODHA_API_SECRET`, `ZERODHA_USER_ID` | Optional |
| `ZERODHA_MODE` | `live` (default) or `paper` |
| `HONBA_CACHE_DIR` | Cache root; Zerodha uses `$HONBA_CACHE_DIR/zerodha` (default `~/.cache/honba/zerodha`) |

```python
from honba_zerodha import ZerodhaAdapter, ZerodhaConfig

ZerodhaConfig.from_env()  # reads ZERODHA_*; from_env(prefix="ALT_") reads ALT_*
ZerodhaAdapter()  # same env lookup; raises AuthenticationError if unset
ZerodhaAdapter(mode="paper")  # no credentials needed; in-memory transport
ZerodhaAdapter(ZerodhaConfig.paper())
```

Bare construction never falls back to paper silently: without credentials in the
environment `ZerodhaAdapter()` raises `AuthenticationError` naming `ZERODHA_API_KEY` and
`ZERODHA_ACCESS_TOKEN`.

## Capabilities

| Dimension | Declared |
|---|---|
| Exchanges | `NSE`, `BSE`, `NFO`, `BFO`, `MCX`, `CDS` |
| Products | `delivery`, `intraday`, `carry` |
| Order types | `market`, `limit`, `stop_limit`, `stop_market` |
| Time in force | `day`, `ioc` |
| Price types | `bid`, `ask`, `last`, `mid` |
| Stream modes | `ltp`, `quote`, `depth` |
| Features | all 14: place/modify/cancel/cancel-all, order and trade book, positions, holdings, funds, margin, quotes, depth, historical bars, instrument master |

`trailing_stop`, `gtc`, `fok` and `gtd` are not declared (Kite validity is `DAY`/`IOC` only).
Unsupported products, order types and time in force raise `CapabilityError` before any
network call. Wire mappings live in `constants.py` as `WireMap`s (`ZERODHA_*_WIRE`);
`assert_covers` checks them against these capabilities at import.

## Tests and certification

From the repository root:

```sh
python -m pytest zerodha/tests                          # offline unit tests
python -m pytest tests/test_contract_suite.py -k zerodha   # certification on recorded fixtures
```

`test_contract_suite[zerodha]` is driven by `tests/fixtures/zerodha/routes.toml`.
