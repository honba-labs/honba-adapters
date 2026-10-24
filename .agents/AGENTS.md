# `honba-adapters` Agent Guidelines & Architecture Invariants

**Subsystem**: Adapters (Pluggable Broker Connectors)
**Language**: Python
**Primary Responsibility**: Pluggable broker adapters for Dhan, Zerodha, Upstox, Angel One, Fyers, Kotak, and IIFL.

---

## 1. Role & Architectural Hints
* `honba-adapters` connects the Honba engine to Indian brokers:
  1. **Broker Isolation**: All broker-specific SDKs, authentication flows, and token models are isolated within each adapter subpackage.
  2. **Anti-Corruption Translation**: Normalizes broker payloads to canonical Honba models.
  3. **Simulated vs. Live Smoke Tests**: Live smoke tests requiring credentials are gated behind environment variables (`HONBA_LIVE_SMOKE_TEST`).
* **Idea Inheritance**:
  - **StockSharp**: Multi-broker connector architecture and credential management.

---

## 2. Coding Guidelines (DDD & TDD)
* **Interface-First**: Adapters adhere to the shared adapter contract.
* **TDD & Parallel Test Directory Rule (MANDATORY)**:
  - Tests live in `tests/` parallel to `src/` (e.g. `tests/test_dhan.py`, `tests/test_zerodha.py`).
* **Check .agents/HANDOFF.md**: Review pending adapter migrations and contract status.

---

## 3. Development & CI Workflow
* **Linting & Formatting**:
  ```bash
  ruff check .
  ruff format --check .
  ```
* **Testing**:
  ```bash
  pytest -q -rs
  ```
