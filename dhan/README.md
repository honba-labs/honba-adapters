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

## Protocol verification

Checked against Dhan's published docs and the official `dhanhq` 2.3.0 client source (read-only,
no API calls, no credentials). Nothing here was exercised against a live account.

| Item | Code value | Source | Verdict |
|---|---|---|---|
| Feed URL | `wss://api-feed.dhan.co?version=2&token&clientId&authType=2` | [live-market-feed](https://dhanhq.co/docs/v2/live-market-feed/), dhanhq `marketfeed.py` | confirmed |
| Request codes | 15/16 ticker, 17/18 quote, 21/22 full, 12 disconnect | live-market-feed, dhanhq `marketfeed.py` | confirmed |
| Max instruments per message | 100 | live-market-feed (also 5000 per connection, 5 connections) | confirmed |
| Header | `<BHBI` (code, length, segment, security id), little-endian | live-market-feed | confirmed |
| Packets | ticker 2 = 16 B, quote 4 = 50 B, full 8 = 162 B (5 x 20 B depth), prev-close 6 and OI 5 not decoded, disconnect 50 = header + int16 | live-market-feed, dhanhq struct formats | confirmed |
| Price type | float32 | live-market-feed | confirmed |
| Segment enum | 0 IDX, 1 NSE_EQ, 2 NSE_FNO, 3 NSE_CURRENCY, 4 BSE_EQ, 5 MCX_COMM, 7 BSE_CURRENCY, 8 BSE_FNO | [annexure](https://dhanhq.co/docs/v2/annexure/) | confirmed |
| Feed LTT epoch (`LTT_EPOCH_OFFSET_S = 0`) | plain UTC epoch seconds | docs say only "EPOCH", no timezone; dhanhq renders it with `fromtimestamp(ltt, UTC)` as HH:MM:SS, implying an IST-shifted value (+19800 s) | unverifiable, kept at 0 |
| Disconnect codes | auth set {806, 807, 808, 809, 810}; 805 (too many connections) excluded | annexure Data API errors, dhanhq 805-809 handling | confirmed |
| REST candle shape | arrays open/high/low/close/volume/timestamp (+open_interest) | [historical-data](https://dhanhq.co/docs/v2/historical-data/) | confirmed |
| REST timestamp | epoch seconds (dhanhq converts with IST tz, so true epoch) | historical-data, dhanhq `convert_to_date_time` | confirmed |
| Intraday intervals | 1, 5, 15, 25, 60 | historical-data | confirmed |
| `INTRADAY_MAX_DAYS` | 90 | historical-data ("only 90 days ... at once"); dhanhq docstring says "last 5 trading day", legacy | confirmed, comment updated |
| Rate limits | data 5/s, orders 10/s | [overview](https://dhanhq.co/docs/v2/) | confirmed |
| Quote API rate | `/marketfeed/*` now its own pool at 1/s (was the 5/s data pool) | overview: Quote APIs 1/s | corrected |
| Errors DH-901..910 | meanings in `http.py` | annexure | confirmed |
| Auth headers | `access-token`, `client-id` | dhanhq `dhan_http.py` (auth docs page names `dhanClientId`, as used in order bodies) | confirmed |
| Token check | `GET /v2/profile` with `access-token` | [authentication](https://dhanhq.co/docs/v2/authentication/), dhanhq `auth.py` | confirmed (adapter probes `/fundlimit`) |

Open issue, not changed: `/marketfeed/quote` request bodies in `data.py` use a single-instrument
object, whereas the docs describe `{"NSE_EQ": [security ids]}`. Verify before using `quote()`/`depth()`.
