# Master Plan: 本場・接 honba-adapters (Python Broker SDK & Adapters)

> **The community and user-facing broker SDK for the Honba platform.**  
> Standardized, typed, pip-installable Python broker adapters implementing the core contract from `honba.adapters` (lifecycle, market data, and execution roles per ADR 0010).  
> Advertises via `honba.adapters` entry points so the trading engine discovers adapters dynamically without coupling to vendor SDKs. Strict zero-float Decimal parsing, recorded broker response fixture replay, boundary isolation, and contract certification across the Indian broker ecosystem.

---

## 0. Executive Summary & Problem Diagnosis: "From Multi-Folder Dump to Production Python SDK"

### 0.1 The Problem: Current Source Code Dump
Historically, `honba-adapters` originated as a flat, uncoordinated directory dump of standalone package attempts. Nine separate folders were scattered directly across the repository root (`angelone/`, `dhan/`, `fyers/`, `iifl/`, `kotak/`, `motilal/`, `shared/`, `upstox/`, `zerodha/`), alongside top-level test files, configs, and scripts.

This resulted in severe architectural and packaging entropy:
1. **Root Directory Sprawl & Fragmented Packaging**: Nine separate directories at the root, each carrying its own `pyproject.toml`, `README.md`, `src/`, and `tests/`. There is **no root `pyproject.toml`**, preventing developers or the CI engine from installing the repository with standard `pip install -e .` or `pip install -e ".[dev]"`.
2. **Hollow Scaffolds & 0-Byte Stub Debris**: Six of the nine broker directories (`angelone`, `fyers`, `iifl`, `kotak`, `motilal`, `upstox`) consist of completely empty, 0-byte stub files (`config.py`, `constants.py`, `data.py`, `execution.py`, `http.py`, `__init__.py`, `instruments.py`, `parsing.py`, `websocket.py`), hollow 4-line `pyproject.toml` files, and empty `tests/` folders.
3. **Split-Brain Test Harness & Fragile PYTHONPATH Hacks**: Test infrastructure is fragmented between a root `tests/` directory (`test_contract_suite.py`, `tests/fixtures/`) and individual package test directories (`dhan/tests/`, `zerodha/tests/`, `shared/tests/`). The root `pytest.ini` relies on fragile path injection (`pythonpath = shared/src dhan/src zerodha/src`), completely ignoring the other broker packages and breaking standard test runners.
4. **Build Artifact & Cache Pollution**: Multiple `.egg-info` directories (`dhan/src/honba_dhan.egg-info`, `zerodha/src/honba_zerodha.egg-info`, `shared/src/honba_adapters_shared.egg-info`) and bytecode directories (`__pycache__`) sit in source folders.
5. **Missing Platform Tooling Parity**: Unlike sibling repositories in the Honba platform (`honba-core`, `honba-te`, `honba-fe`), `honba-adapters` lacks a standard `Makefile` with canonical developer targets (`dev`, `test`, `lint`, `clean`).
6. **Uneven Broker Coverage**: Only two brokers (`honba-dhan`, `honba-zerodha`) are fully implemented and contract-certified, leaving dominant market players (**Angel One** and **Upstox**) completely unbacked.

### 0.2 The Solution: The Clean Python Packaging Architecture
To eliminate the root dump and bring `honba-adapters` in line with modern Python packaging standards (PEP 517, PEP 518, PEP 621, PEP 561), the repository undergoes a **complete packaging reform and source code restructuring**.

Every file in the repository resides in one of four strictly segregated zones:
- **Zone 1: Production Python SDK (`src/honba_adapters/`)**: A single, unified, typed, pip-installable Python package exposing all adapters and shared infrastructure under a cohesive namespace, with optional extras (`[dhan]`, `[zerodha]`, `[angelone]`, `[upstox]`, `[all]`) and dynamic entry-point registration. Backward-compatibility shims (`honba_dhan`, `honba_zerodha`, `honba_adapters_shared`) ensure zero breaking changes for existing callers.
- **Zone 2: Centralized Test Suite & Golden Fixtures (`tests/`)**: A unified test tree (`tests/contract/`, `tests/unit/`, `tests/fixtures/`, `tests/property/`) with offline HTTP/WebSocket replay and contract verification against ADR 0010, governed by declarative pytest configuration in `pyproject.toml`.
- **Zone 3: Connection Profiles & Configs (`configs/`)**: Canonical paper and live TOML configuration templates for supported broker adapters.
- **Zone 4: Developer Tools, Smoke Runners & Examples (`scripts/`, `examples/`)**: Guarded live/sandbox smoke testing harnesses, fixture recording tools, and runnable usage scripts.

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      honba-adapters Python SDK                         │
 ├────────────────────────────┬───────────────────────────────────────────┤
 │ Common Infrastructure      │ Fixture Replay, Contract Suite, TOTP, WS  │
 │ Priority 1: DhanHQ         │ Access token, HTTP REST, Binary WS Stream │
 │ Priority 1: Zerodha Kite   │ OAuth2 session, KiteTicker, Postback      │
 │ Priority 1: Angel One      │ SmartAPI 2.0, Headless TOTP, SmartStream  │
 │ Priority 1: Upstox Uplink  │ Uplink v2, OAuth2 Refresh, Protobuf Feed  │
 │ Priority 2: Fyers API v3   │ OAuth2, Order Routing, Data Socket        │
 │ Priority 2: Kotak Neo      │ Neo API, Session Auth, Quote Stream       │
 │ Priority 3: IIFL / Motilal │ Client API, Execution & Position Feeds    │
 ├────────────────────────────┴───────────────────────────────────────────┤
 │ Contract: honba.adapters (ADR 0010: Lifecycle, MarketData, Execution)  │
 ├────────────────────────────────────────────────────────────────────────┤
 │ Runtime & Kernel: honba (honba-tamashii) + Decimal Math (honba-core)   │
 └────────────────────────────────────────────────────────────────────────┘
```

### 0.3 Adapter Maturity & Conformance Audit

| Package / Module | Broker | Auth Mechanism | Current State | Target Architecture Priority | Severity |
|:---|:---|:---|:---|:---|:---|
| `honba_adapters.dhan` | DhanHQ | Access token | Implemented & contract-certified | Maintain, add depth streaming | Closed |
| `honba_adapters.zerodha` | Zerodha Kite Connect v3 | OAuth2 / KiteTicker | Implemented & contract-certified | Maintain, add postback handler | Closed |
| `honba_adapters.angelone` | Angel One SmartAPI 2.0 | TOTP + API Key | **Package scaffold only** | **Priority 1**: Implement execution, quote, SmartStream | **P0 (Critical)** |
| `honba_adapters.upstox` | Upstox Uplink v2 | OAuth2 / Proto Feed | **Package scaffold only** | **Priority 1**: Implement execution, quote, Proto stream | **P0 (Critical)** |
| `honba_adapters.fyers` | Fyers API v3 | OAuth2 / Data Socket | Package scaffold only | **Priority 2**: Implement execution & data | **P1 (High)** |
| `honba_adapters.kotak` | Kotak Neo | OAuth2 / Neo API | Package scaffold only | **Priority 2**: Implement execution & data | **P1 (High)** |
| `honba_adapters.iifl` | IIFL Markets | Client API | Package scaffold only | **Priority 3**: Implement execution & data | **P2 (Medium)** |
| `honba_adapters.motilal` | Motilal Oswal | Rise API | Package scaffold only | **Priority 3**: Implement execution & data | **P2 (Medium)** |
| `honba_adapters.common` | Shared infra | — | Rate limiters, TOTP, WireMap | Consolidate, expand fixture replay & contracts | **P0 (Critical)** |

---

## 1. Architectural Invariants & Non-Negotiable Contract Rules

To guarantee platform stability, deterministic testability, and zero runtime surprises, every adapter in `honba-adapters` must strictly adhere to six non-negotiable architectural invariants:

### 1.1 Strict Contract Adherence (`honba.adapters.contract` & ADR 0010)
Every adapter satisfies the canonical behavioral contract defined in `honba.adapters`:
- **Three-Role Unified Facade**: A single adapter class implements `Adapter` (lifecycle), `MarketDataAdapter` (market data), and `ExecutionAdapter` (order routing and portfolio).
- **Pre-Flight Capability Verification**: If an operation is unsupported by a broker (e.g. `OrderType.TRAILING_STOP` or bracket orders), the adapter **must raise `CapabilityError` before making any network call**.
- **Rejection as `OrderReport`, Never Exceptions**: Business-logic refusals and broker order rejections (e.g. margin shortfall, freeze limit breach, circuit halt) must return `OrderReport(status=OrderStatus.REJECTED)` with explicit reasons; network exceptions are reserved exclusively for transport-level failures.
- **Contract Verification Gate**: Every adapter must pass `assert_adapter_contract(adapter)`.

### 1.2 Boundary Isolation & Zero Vendor Leaks (`find_boundary_violations`)
Broker vendor SDKs (e.g. `kiteconnect`, `smartapi-python`, `upstox-python-sdk`) and raw vendor wire dictionaries/Protobuf structures **must never leak beyond the adapter boundary**.
- All incoming broker payloads are immediately parsed into `honba_core` / `honba.adapters` domain models.
- Core runtime components (`honba-te`, `honba-ya`, `honba-tamashii`) interact exclusively with canonical types (`Order`, `OrderReport`, `Quote`, `Bar`, `Position`, `Holding`, `MarginSummary`).
- Verified via automated boundary checking (`find_boundary_violations`).

### 1.3 Zero Floating-Point Precision Loss
Prices, quantities, notionals, commissions, and cash values are mapped strictly to Python `decimal.Decimal` and 128-bit fixed-point Rust types:
- **Zero Float Conversion**: Floating-point parsing (`float(val)`) on financial numbers is strictly forbidden.
- String-to-Decimal conversions preserve exact tick sizes and lot increments.

### 1.4 Recorded Response Golden Fixtures & Offline Replay
Every implemented adapter must carry authentic, anonymized HTTP and WebSocket response recordings under `tests/fixtures/<broker>/`:
- **Deterministic Offline Testing**: Unit and contract test suites execute 100% offline in CI without hitting external broker endpoints or requiring live credentials.
- **Credential Stripping**: All recorded payloads are scrubbed of API keys, client IDs, tokens, PAN numbers, and personal identifiers.
- **Longest-Prefix Route Matching**: Offline replay matches endpoints via `routes.toml` using longest path-fragment matching.

### 1.5 Dynamic Entry-Point Discovery
All adapters advertise themselves through the `honba.adapters` entry-point group in the root `pyproject.toml`:
```toml
[project.entry-points."honba.adapters"]
dhan = "honba_adapters.dhan:DhanAdapter"
zerodha = "honba_adapters.zerodha:ZerodhaAdapter"
angelone = "honba_adapters.angelone:AngelOneAdapter"
upstox = "honba_adapters.upstox:UpstoxAdapter"
```
The trading engine resolves adapters dynamically by name (`resolve_adapter("dhan")`), completely decoupling the engine from vendor implementations.

### 1.6 Modern PEP 517 / PEP 621 Python Packaging
- Root `pyproject.toml` using `setuptools` (with `find_packages` under `src/`).
- Standard optional extras for selective dependencies: `pip install -e ".[dhan,zerodha]"`, `pip install -e ".[dev]"`, or `pip install -e ".[all]"`.
- Typed public APIs with `py.typed` marker (PEP 561) and 100% passing `ruff` and `mypy` checks.
- Zero root-level test scripts or hacky `pythonpath` injections in `pytest.ini`.

---

## 2. Repository Restructuring: Before vs After (The Reform)

### 2.1 The Current State: "The Multi-Folder Dump"
```text
honba-adapters/ (CURRENT DUMP)
├── angelone/                       # DUMP: 0-byte scaffold files, empty tests
│   ├── pyproject.toml              # Hollow 4-line pyproject
│   ├── README.md
│   ├── src/honba_angelone/         # 9 empty 0-byte python files
│   └── tests/                      # Empty test directory
├── configs/                        # Dumped at root
│   ├── dhan_live.toml
│   ├── dhan_paper.toml
│   ├── zerodha_live.toml
│   └── zerodha_paper.toml
├── dhan/                           # Standalone package folder
│   ├── pyproject.toml
│   ├── src/honba_dhan/
│   │   └── honba_dhan.egg-info/    # Build artifact committed/generated in src
│   └── tests/
├── examples/                       # Dumped at root with __pycache__
│   ├── 01_connect_dhan.py
│   └── 04_place_order_paper.py
├── fyers/                          # DUMP: 0-byte scaffold
├── iifl/                           # DUMP: 0-byte scaffold
├── kotak/                          # DUMP: 0-byte scaffold
├── motilal/                        # DUMP: 0-byte scaffold
├── pytest.ini                      # Hardcoded pythonpath = shared/src dhan/src zerodha/src
├── README.md
├── scripts/                        # Dumped at root
│   └── smoke_test.py
├── shared/                         # Standalone shared package
│   ├── pyproject.toml
│   ├── src/honba_adapters_shared/
│   │   └── honba_adapters_shared.egg-info/
│   └── tests/
├── tests/                          # Root-level split-brain test folder
│   ├── fixtures/                   # Golden fixtures dumped at root
│   └── test_contract_suite.py
├── upstox/                         # DUMP: 0-byte scaffold
└── zerodha/                        # Standalone package folder
    ├── pyproject.toml
    ├── src/honba_zerodha/
    └── tests/
```

### 2.2 The Clean Target State: "The 4-Zone Production Architecture"
```text
honba-adapters/ (CLEAN RESTRUCTURED TARGET)
├── pyproject.toml                  # Single source of truth: build, deps, extras, ruff, mypy, pytest
├── Makefile                        # Standard platform targets: dev, test, lint, clean
├── README.md                       # Comprehensive SDK guide, installation, capability matrix
├── LICENSE                         # License definition
├── MIGRATION.md                    # Adapter migration roadmap & contract certification notes
│
├── src/                            # ZONE 1: Production Python SDK (honba_adapters)
│   ├── honba_adapters/
│   │   ├── __init__.py             # Public exports: registry helpers, __version__, list_adapters
│   │   ├── py.typed                # PEP 561 typing marker
│   │   │
│   │   ├── common/                 # Shared adapter infrastructure & utilities
│   │   │   ├── __init__.py
│   │   │   ├── auth.py             # Headless RFC 6238 TOTP, TokenManager, FileTokenStore
│   │   │   ├── boundary.py         # Boundary check utilities (find_boundary_violations)
│   │   │   ├── contract.py         # Contract test assertion harness (assert_adapter_contract)
│   │   │   ├── errors.py           # Canonical adapter error hierarchy (CapabilityError, etc.)
│   │   │   ├── fixtures.py         # load_fixture, recorded_transport, route matchers
│   │   │   ├── maps.py             # WireMap, assert_covers bidirectional translators
│   │   │   ├── rate_limit.py       # TokenBucket, RateLimiter, rate_limited decorators
│   │   │   └── websocket.py        # ReconnectingWebSocketClient, SequenceTracker
│   │   │
│   │   ├── dhan/                   # DhanHQ Adapter Implementation
│   │   │   ├── __init__.py         # DhanAdapter export
│   │   │   ├── config.py           # DhanConfig (from_env, paper, live)
│   │   │   ├── constants.py        # Exchange strings, order types, segments
│   │   │   ├── data.py             # DhanMarketData: quotes, depth, historical
│   │   │   ├── execution.py        # DhanExecution: order routing, positions, funds
│   │   │   ├── http.py             # HTTP transport client with auth injection
│   │   │   ├── instruments.py      # Dhan master security lookup & symbol cache
│   │   │   ├── mock.py             # In-memory mock transport for paper trading
│   │   │   ├── parsing.py          # Zero-float Decimal response parsers
│   │   │   └── websocket.py        # Dhan binary market feed & order stream
│   │   │
│   │   ├── zerodha/                # Zerodha Kite Connect v3 Adapter Implementation
│   │   │   ├── __init__.py         # ZerodhaAdapter export
│   │   │   ├── config.py           # ZerodhaConfig (from_env, paper, live)
│   │   │   ├── constants.py        # Kite segment, variety, validity mappings
│   │   │   ├── data.py             # ZerodhaMarketData: quotes, candles, instruments
│   │   │   ├── execution.py        # ZerodhaExecution: orders, trades, margins
│   │   │   ├── http.py             # Kite REST transport client
│   │   │   ├── instruments.py      # CSV instrument cache & token resolver
│   │   │   ├── mock.py             # In-memory mock transport for paper trading
│   │   │   ├── parsing.py          # Decimal parsing & Kite response decoders
│   │   │   └── websocket.py        # KiteTicker binary WebSocket decoder
│   │   │
│   │   ├── angelone/               # Angel One SmartAPI 2.0 (Priority 1 - Full Implementation)
│   │   │   ├── __init__.py         # AngelOneAdapter export
│   │   │   ├── config.py           # AngelOneConfig (API key, client code, TOTP secret)
│   │   │   ├── constants.py        # SmartAPI exchange codes, transaction types
│   │   │   ├── data.py             # AngelOneMarketData: quotes, historical candles
│   │   │   ├── execution.py        # AngelOneExecution: order placement, status, holdings
│   │   │   ├── http.py             # SmartAPI authenticated HTTP transport
│   │   │   ├── instruments.py      # OpenAPIScripMaster JSON lookup & parser
│   │   │   ├── mock.py             # In-memory mock transport for paper trading
│   │   │   ├── parsing.py          # Error taxonomy & exact Decimal parsing
│   │   │   └── websocket.py        # SmartStream binary WebSocket client
│   │   │
│   │   ├── upstox/                 # Upstox Uplink v2 (Priority 1 - Full Implementation)
│   │   │   ├── __init__.py         # UpstoxAdapter export
│   │   │   ├── config.py           # UpstoxConfig (OAuth2 client ID, secret, redirect)
│   │   │   ├── constants.py        # Instrument keys, order modes, validity
│   │   │   ├── data.py             # UpstoxMarketData: market quotes, intraday bars
│   │   │   ├── execution.py        # UpstoxExecution: order lifecycle & portfolio
│   │   │   ├── http.py             # Upstox v2 REST transport with bearer token refresh
│   │   │   ├── instruments.py      # GZipped JSON instrument master loader
│   │   │   ├── mock.py             # In-memory mock transport for paper trading
│   │   │   ├── parsing.py          # Strict Decimal parsers & error taxonomy
│   │   │   └── websocket.py        # Upstox Protobuf market stream & order updates
│   │   │
│   │   ├── fyers/                  # Fyers API v3 (Priority 2)
│   │   ├── kotak/                  # Kotak Neo API (Priority 2)
│   │   ├── iifl/                   # IIFL Markets (Priority 3)
│   │   └── motilal/                # Motilal Oswal Rise (Priority 3)
│   │
│   ├── honba_dhan/                 # Backward-compatibility alias -> honba_adapters.dhan
│   │   └── __init__.py
│   ├── honba_zerodha/              # Backward-compatibility alias -> honba_adapters.zerodha
│   │   └── __init__.py
│   └── honba_adapters_shared/      # Backward-compatibility alias -> honba_adapters.common
│       └── __init__.py
│
├── configs/                        # ZONE 2: Canonical Adapter Configuration Profiles
│   ├── dhan_live.toml              # Dhan live environment profile
│   ├── dhan_paper.toml             # Dhan paper sandbox profile
│   ├── zerodha_live.toml           # Zerodha live environment profile
│   ├── zerodha_paper.toml          # Zerodha paper sandbox profile
│   ├── angelone_live.toml          # Angel One live environment profile
│   ├── angelone_paper.toml         # Angel One paper sandbox profile
│   ├── upstox_live.toml            # Upstox live environment profile
│   └── upstox_paper.toml           # Upstox paper sandbox profile
│
├── tests/                          # ZONE 3: Centralized Test Suite & Replay Fixtures
│   ├── conftest.py                 # Central pytest fixtures, mock transports, event loops
│   ├── contract/                   # Core contract suite verification (ADR 0010)
│   │   ├── test_contract_suite.py  # Runs verify_adapter_contract over all registered adapters
│   │   └── test_discovery.py       # Validates honba.adapters entry-point dynamic resolution
│   ├── unit/                       # Unit test suites isolated by module
│   │   ├── common/                 # Tests for rate limiter, auth, websocket, WireMap
│   │   ├── dhan/                   # Dhan unit tests: parsing, mappings, ws, drivers
│   │   ├── zerodha/                # Zerodha unit tests: parsing, ticker, mappings
│   │   ├── angelone/               # Angel One unit tests: auth, execution, SmartStream
│   │   └── upstox/                 # Upstox unit tests: OAuth, Decimal execution, protobuf
│   ├── fixtures/                   # Golden recorded response replays
│   │   ├── dhan/                   # Anonymized Dhan JSON responses + routes.toml
│   │   ├── zerodha/                # Anonymized Zerodha JSON responses + routes.toml
│   │   ├── angelone/               # Anonymized Angel One JSON responses + routes.toml
│   │   └── upstox/                 # Anonymized Upstox JSON/Proto responses + routes.toml
│   └── property/                   # Property-based invariant testing (Hypothesis)
│       ├── test_decimal_invariants.py
│       └── test_boundary_isolation.py
│
├── examples/                       # ZONE 4: Runnable Usage Demonstrations & Workflows
│   ├── README.md
│   ├── 01_connect_dhan.py          # Live/paper connection to DhanHQ
│   ├── 02_connect_zerodha.py       # Live/paper connection to Zerodha Kite
│   ├── 03_connect_angelone.py      # Headless TOTP login & quote stream with Angel One
│   ├── 04_connect_upstox.py        # OAuth2 token flow & live quote stream with Upstox
│   └── 05_paper_execution.py       # Multi-broker paper order routing & fill simulation
│
└── scripts/                        # Developer Tooling & Verification Scripts
    ├── smoke_test.py               # Live/sandbox credentialed smoke test runner
    └── record_fixtures.py          # Session recorder to capture anonymized golden responses
```

---

## 3. Phase-by-Phase Implementation Roadmap

The reform and implementation of `honba-adapters` follows an ordered, bottom-up 7-phase roadmap:

```text
Phase R: Packaging Reform ──▶ Unified pyproject.toml, purge root dump, create src/honba_adapters/
Phase 1: Common Infra     ──▶ Hardened fixture replayer, TOTP engine, contract assertions
Phase 2: Angel One (P0)   ──▶ SmartAPI 2.0 headless TOTP login, execution, SmartStream WS
Phase 3: Upstox (P0)      ──▶ Uplink v2 OAuth2 token refresh, Decimal execution, Proto feed
Phase 4: Tier 2 Brokers   ──▶ Fyers API v3, Kotak Neo, IIFL, Motilal Oswal
Phase 5: Hypothesis & Fuzz──▶ Property-based parsing tests, boundary check linter
Phase 6: SemVer & Release ──▶ Automated compatibility validation against honba core releases
```

---

### Phase R: Source Code Restructuring & Python Packaging Unification (The Reform)

- [x] **R.1 Unified PEP 517 / PEP 621 `pyproject.toml`**
  - [x] Create root `pyproject.toml` defining package `honba-adapters` under `setuptools>=68`.
  - [x] Configure optional dependency extras: `dhan`, `zerodha`, `angelone`, `upstox`, `fyers`, `kotak`, `all`, and `dev`.
  - [x] Declare dynamic entry points for `honba.adapters` discovering all adapters.
  - [x] Consolidate linting (`tool.ruff`), typing (`tool.mypy`), and testing (`tool.pytest.ini_options`) directly inside `pyproject.toml`.
  - [x] Eliminate the root `pytest.ini` and its fragile `pythonpath` hacks.

- [x] **R.2 Purge Root Directory Dump & 0-Byte Scaffolds**
  - [x] Remove all 0-byte hollow files and scaffold folders from root (`fyers/`, `iifl/`, `kotak/`, `motilal/`).
  - [x] Purge committed/generated `.egg-info` directories and `__pycache__` directories across the tree.
  - [x] Eliminate root-level duplicate config dumps and normalize into `configs/`.

- [x] **R.3 Source Code Migration to Unified Namespace (`src/honba_adapters/`)**
  - [x] Migrate `shared/src/honba_adapters_shared/` into `src/honba_adapters/common/`.
  - [x] Migrate `dhan/src/honba_dhan/` into `src/honba_adapters/dhan/`.
  - [x] Migrate `zerodha/src/honba_zerodha/` into `src/honba_adapters/zerodha/`.
  - [x] Create backward-compatibility shims (`src/honba_dhan/`, `src/honba_zerodha/`, `src/honba_adapters_shared/`) re-exporting the unified namespace so external dependencies never break.

- [x] **R.4 Test Suite Consolidation & Golden Fixture Relocation**
  - [x] Consolidate all tests into `tests/`: `tests/unit/common/`, `tests/unit/dhan/`, `tests/unit/zerodha/`, `tests/contract/`.
  - [x] Move golden fixtures to `tests/fixtures/dhan/` and `tests/fixtures/zerodha/`.
  - [x] Verify `pytest` runs cleanly from the repo root with zero PYTHONPATH workarounds.

- [x] **R.5 Platform Makefile & Developer Tooling Parity**
  - [x] Create root `Makefile` with standard platform targets: `dev`, `test`, `test-live`, `lint`, `fmt`, `clean`.
  - [x] Add pre-commit check scripts and verification commands.

---

### Phase 1: Shared Infrastructure & Recorded Fixture Replay Harness (`honba_adapters.common`)

- [x] **1.1 Recorded Response Fixture Replayer (`common/fixtures.py`)**
  - [x] Support offline HTTP mock playback with longest path-fragment route matching via `routes.toml`.
  - [x] Implement recorded WebSocket event stream playback for offline market data tests (`RecordedWebSocketClient`).
  - [x] Provide `load_fixture(broker, filename)` helper returning exact parsed JSON, text, or binary payloads.

- [x] **1.2 Anonymized Session Recorder (`scripts/record_fixtures.py`)**
  - [x] Build interactive session recorder to capture real HTTP REST calls and WebSocket frames.
  - [x] Implement automated regex sanitizers to strip `access_token`, `client_id`, `api_key`, `pan`, and user credentials before writing to disk.

- [x] **1.3 Contract Test Suite Hardening (`common/contract.py`)**
  - [x] Enforce complete lifecycle checks: `connect()`, `disconnect()`, `session()`, `is_connected()`, `require_connected()`.
  - [x] Enforce market data checks: `instruments()`, `search_instruments()`, `quote()`, `depth()`, `historical_bars()`, `subscribe()`, `unsubscribe()`.
  - [x] Enforce execution checks: `place_order()`, `modify_order()`, `cancel_order()`, `cancel_all()`, `orders()`, `positions()`, `holdings()`, `funds()`, `margin()`.
  - [x] Verify `CapabilityError` is raised before network dispatch for undeclared broker operations.

- [x] **1.4 Rate Limiting & Headless TOTP Engine (`common/rate_limit.py`, `common/auth.py`)**
  - [x] Token-bucket rate limiter with injectable clock supporting sub-millisecond throttle intervals.
  - [x] Headless RFC 6238 TOTP generator and automated file/env token manager with expiry awareness.

---

### Phase 2: Angel One SmartAPI 2.0 Full Implementation (`honba_adapters.angelone`) — Priority 1 (P0)

- [x] **2.1 Authentication & Morning Login Session (`angelone/config.py`, `angelone/http.py`)**
  - [x] Implement `AngelOneConfig` loading `ANGELONE_API_KEY`, `ANGELONE_CLIENT_CODE`, `ANGELONE_PASSWORD`, `ANGELONE_TOTP_KEY`.
  - [x] Headless RFC 6238 TOTP morning login flow generating JWT session token and refresh token.
  - [x] Mandatory header injection on every request: `X-PrivateKey`, `X-SourceID=WEB`, `X-ClientLocalIP`, `X-ClientPublicIP`, `X-MACAddress`, Bearer authorization.
  - [x] Token refresh flow on HTTP 401 / token expiry.

- [x] **2.2 Execution Role (`angelone/execution.py`)**
  - [x] Implement `place_order`: map `Order` -> SmartAPI payload (`variety=NORMAL`, `tradingsymbol`, `symboltoken`, `transactiontype=BUY/SELL`, `exchange=NSE/BSE/NFO`, `ordertype=MARKET/LIMIT/STOPLOSS_LIMIT`, `producttype=INTRADAY/DELIVERY/CARRYFORWARD`, `duration=DAY/IOC`).
  - [x] Implement `modify_order`, `cancel_order`, `cancel_all`.
  - [x] Implement account queries: `orders()`, `trades()`, `positions()`, `holdings()`, `funds()`, `margin()`.
  - [x] Comprehensive error code taxonomy mapping (`AB1004`, `AB2001`, `AG8001`, `AG8002`, `AG8003`) into canonical `OrderReport(status=REJECTED)` with explicit diagnostic messages.

- [x] **2.3 Market Data & SmartStream WebSocket (`angelone/data.py`, `angelone/websocket.py`)**
  - [x] Scrip master downloader: parse OpenAPIScripMaster JSON and maintain symbol-to-token cache.
  - [x] Implement `quote()`, `depth()` (5-level best bid/ask), and `historical_bars()` (candle intervals: 1m, 5m, 15m, 1d).
  - [x] SmartStream binary WebSocket client: decode 20-byte, 44-byte, and 124-byte binary packets into canonical `Quote` and `Depth` objects.
  - [x] Dynamic subscription management (`subscribe()`, `unsubscribe()`).

- [x] **2.4 Recorded Golden Fixtures & Contract Certification**
  - [x] Record golden fixtures in `tests/fixtures/angelone/` (`login.json`, `profile.json`, `funds.json`, `positions.json`, `orders.json`, `quote.json`, `routes.toml`).
  - [x] Implement `tests/unit/angelone/test_contract.py` validating full ADR 0010 contract adherence.
  - [x] Register entry point in `pyproject.toml`: `angelone = "honba_adapters.angelone:AngelOneAdapter"`.

---

### Phase 3: Upstox Uplink v2 Full Implementation (`honba_adapters.upstox`) — Priority 1 (P0)

- [ ] **3.1 Authentication & Configuration (`upstox/config.py`, `upstox/http.py`)**
  - [ ] Implement `UpstoxConfig` loading `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URI`, `UPSTOX_ACCESS_TOKEN`.
  - [ ] Automated OAuth2 token refresh flow using `refresh_token` grant.
  - [ ] Upstox instrument key translation: `NSE_EQ|INE002A01018`, `NSE_FO|43212`, `MCX_FO|...` to `honba_core` canonical `InstrumentKey`.

- [ ] **3.2 Execution Role (`upstox/execution.py`)**
  - [ ] Implement order placement: exact Decimal serialization (`price`, `trigger_price`, `quantity`), slicing support for freeze bounds.
  - [ ] Implement `modify_order`, `cancel_order`, `cancel_all`.
  - [ ] Implement `orders()`, `trades()`, `positions()`, `holdings()`, and `margin()` queries.
  - [ ] Rejection mapping: convert Upstox HTTP 400/422 status codes and error payloads into canonical `OrderReport` rejections.

- [ ] **3.3 Protobuf Market Data Feed & Order WebSocket (`upstox/data.py`, `upstox/websocket.py`)**
  - [ ] GZipped JSON instrument master loader and cache manager.
  - [ ] REST quote, multi-scrip quote, and historical OHLCV bar fetcher.
  - [ ] Binary Protobuf WebSocket client: decode `MarketDataFeed.proto` real-time feed (LTP, Quote, Full 5/30-depth).
  - [ ] Portfolio stream: real-time order update notifications and fill broadcasts.

- [ ] **3.4 Recorded Golden Fixtures & Contract Certification**
  - [ ] Record golden fixtures in `tests/fixtures/upstox/` (`token.json`, `profile.json`, `funds.json`, `positions.json`, `orders.json`, `quote.json`, `routes.toml`).
  - [ ] Implement `tests/unit/upstox/test_contract.py` validating ADR 0010 contract adherence.
  - [ ] Register entry point in `pyproject.toml`: `upstox = "honba_adapters.upstox:UpstoxAdapter"`.

---

### Phase 4: Secondary Tier Adapters (Fyers, Kotak Neo, IIFL, Motilal Oswal) — Priority 2 & 3

- [ ] **4.1 Fyers API v3 (`honba_adapters.fyers`) — Priority 2**
  - [ ] Implement OAuth2 authorization and session handling.
  - [ ] Implement execution role: order routing, positions, and margin.
  - [ ] Implement binary Data Socket WebSocket for NSE/BSE quote feeds.
  - [ ] Golden fixtures and contract certification.

- [ ] **4.2 Kotak Neo API (`honba_adapters.kotak`) — Priority 2**
  - [ ] Implement Neo API session authentication (Consumer Key, Consumer Secret, Mobile, MPIN, TOTP).
  - [ ] Implement execution role with Neo order routing and report polling.
  - [ ] Implement quote and order update WebSocket streams.
  - [ ] Golden fixtures and contract certification.

- [ ] **4.3 IIFL Markets & Motilal Oswal (`honba_adapters.iifl`, `honba_adapters.motilal`) — Priority 3**
  - [ ] Implement client API session login and token exchange.
  - [ ] Implement order routing and position reconciliation.
  - [ ] Golden fixtures and contract certification.

---

### Phase 5: Hypothesis Property-Based Testing, Fuzzing & SemVer Stability

- [x] **5.1 Property-Based Wire Parsing Invariants (Hypothesis)**
  - [x] Test all wire response parsers against randomized, malformed, and edge-case broker payloads.
  - [x] Guarantee 0 unhandled `KeyError`, `IndexError`, or `TypeError` crashes during wire ingestion.

- [x] **5.2 Decimal Precision Loss Fuzzing**
  - [x] Generate randomized float string representations with arbitrary tick sizes (up to 8 decimal places).
  - [x] Prove: `serialize(deserialize(payload)) == payload` with zero floating-point approximation error.

- [x] **5.3 Boundary Check Enforcement**
  - [x] Run `find_boundary_violations` across all public adapter APIs.
  - [x] Prove: 0 broker vendor types, raw dictionaries, or unmapped enums leak to callers.

- [x] **5.4 SemVer & Cross-Repo Contract Compatibility CI Gates**
  - [x] Run adapter contract verification in CI against `honba` core releases before publication.
  - [x] Automatic check ensuring all registered entry points match exported adapter classes.

---

## 4. Verification & CI Quality Gates

Every pull request and release build must pass the following verification pipeline:

```bash
# 1. Formatting and linting (0 warnings allowed)
make lint
# Or directly:
ruff check src/ tests/ examples/ scripts/
ruff format --check src/ tests/ examples/ scripts/

# 2. Strict type checking
make typecheck
# Or directly:
mypy src/honba_adapters

# 3. Contract certification across all implemented adapters (100% offline via golden fixtures)
make test
# Or directly:
pytest tests/contract/ -v

# 4. Unit test suite across individual adapter modules
pytest tests/unit/ -v

# 5. Property-based invariants & boundary checks
pytest tests/property/ -v

# 6. Optional: Live / sandbox credentialed smoke tests (guarded)
DHAN_MODE=paper python scripts/smoke_test.py --adapter dhan
```
