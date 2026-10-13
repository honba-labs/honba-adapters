# Adapter contract migration (E1-S1b)

Every broker package in this repository must implement the E1-S1 adapter contract
([ADR 0010](../honba/docs/adr/0010-adapter-contract.md)): a lifecycle facade plus a
market-data role and an execution role, advertised through the `honba.adapters` entry-point
group. "Implemented" means it passes the one canonical behavioural suite
(`honba.adapters.contract`) with its account and market-data reads served from **recorded
broker responses** checked in under `tests/fixtures/`.

This note records where each package is and what a scaffold still needs. It is the
per-adapter migration note owed by the roadmap (section 6, `honba-adapters`).

## Status

| Package | Registry name | Facade + roles | Entry point | Recorded fixtures | Contract-certified |
|---|---|---|---|---|---|
| `dhan/` | `dhan` | yes | yes | yes (`tests/fixtures/dhan/`) | yes |
| `zerodha/` | `zerodha` | yes | yes | yes (`tests/fixtures/zerodha/`) | yes |
| `angelone/` | `angelone` | scaffold only | no | no | no |
| `fyers/` | `fyers` | scaffold only | no | no | no |
| `upstox/` | `upstox` | scaffold only | no | no | no |
| `kotak/` | `kotak` | scaffold only | no | no | no |
| `iifl/` | `iifl` | scaffold only | no | no | no |
| `motilal/` | `motilal` | scaffold only | no | no | no |

The six scaffold packages have the uniform layout (`config.py`, `data.py`, `execution.py`,
`instruments.py`, `websocket.py`, `http.py`, `parsing.py`, `constants.py`) but an **empty**
`__init__.py` and no `[project.entry-points."honba.adapters"]` declaration. Until they are
implemented, listing them in a run config fails with `AdapterNotFound` naming the installed
adapters (`dhan`, `zerodha`), which is the intended honest failure.

## Certification defined

A package is certified when, from the repository root:

```sh
python -m pytest tests/test_contract_suite.py
```

lists it under `test_contract_suite[<name>]` and that test passes. The shared suite in
`tests/test_contract_suite.py` runs `honba.adapters.contract.verify_adapter_contract` over
each adapter with a transport built by `honba_adapters_shared.recorded_transport`: the
read-only account and market-data routes are replayed from `tests/fixtures/<name>/`, and the
stateful order lifecycle falls through to the package's in-memory paper handler
(`make_<name>_mock_handler`). `test_adapter_wiring_recorded_fixture` additionally proves the
registry resolves the adapter and that a read returns the *recorded* value.

## Migrating a scaffold package

1. Implement the facade and both roles on one object (start from `dhan/` as the template),
   with `capabilities()` naming the broker and declaring exactly what it supports.
2. Implement pre-flight capability checks: an unsupported request raises `CapabilityError`
   **before** any network call, and a broker refusal comes back as an `OrderReport` with
   `status=REJECTED`, never as an exception.
3. Record broker responses into `<package>/tests/fixtures/<name>/` (one file per read
   endpoint) and add a `<package>/tests/conftest.py` fixture that builds the adapter with a
   transport over them. Save the responses from real API calls or documented examples.
4. Add the package's own `tests/test_contract.py` (a fast, mock-backed run), parsing tests
   that use `load_fixture`, and error-path tests.
5. Add the package to `_BUILDERS` and the route maps in `tests/test_contract_suite.py`, and
   to the root `pytest.ini` `testpaths` if it gets its own test root.
6. Declare the entry point in `pyproject.toml` and add the package to `.github/workflows/ci.yml`.
7. Add the package to the install list in `README.md` and to the CI `pip install`.

## Adding another recorded adapter to the shared suite

Declare the read routes and point the builder at the package's handler:

```python
from honba_adapters_shared import recorded_transport

_ROUTES = {
    "/quote": "mybroker/quote.json",
    "/portfolio/positions": "mybroker/positions.json",
}


def _mybroker():
    return MyBrokerAdapter(
        MyBrokerConfig.paper(),
        http_transport=recorded_transport(
            _ROUTES, fixtures=FIXTURES, fallback=make_mybroker_mock_handler()
        ),
    )
```

`recorded_transport` matches a request by the **longest** path fragment contained in its
path, so `/instruments/historical/738561/day` is served by `/instruments/historical` and not
by the shorter `/instruments`. A `.json` fixture is a 200 JSON body; `.csv` is 200 text.
Any request no fragment matches goes to `fallback`.
