"""The pytest-facing wrapper around the core adapter contract suite.

The core suite in honba.adapters.contract is pytest-free on purpose: it is also run by
the engine's own checks and by scripts. This module provides the hardened assertion harness
validating complete ADR 0010 contract adherence:
- Pre-flight capability checks
- Name and capability verification
- Lifecycle connection state verification
- Behavioral rule compliance
"""

from __future__ import annotations

from typing import Any

from honba.adapters.base import Adapter
from honba.adapters.capabilities import AdapterCapabilities
from honba.adapters.contract import ContractViolation, verify_adapter_contract

__all__ = ["ContractViolation", "assert_adapter_contract"]


async def assert_adapter_contract(adapter: Adapter, **kwargs: Any) -> None:
    """Assert ``adapter`` satisfies the whole contract, or raise :class:`ContractViolation`.

    Hardened checks:
    - Verifies adapter has a non-empty name string
    - Verifies adapter.capabilities() returns an AdapterCapabilities instance
    - Verifies initial disconnected state
    - Executes verify_adapter_contract with behavioral assertions
    - Verifies clean teardown disconnect state
    """
    if (
        not hasattr(adapter, "name")
        or not isinstance(adapter.name, str)
        or not adapter.name.strip()
    ):
        raise ContractViolation(
            f"{type(adapter).__name__} violates contract: name must be a non-empty string"
        )

    caps = adapter.capabilities()
    if not isinstance(caps, AdapterCapabilities):
        raise ContractViolation(
            f"{type(adapter).__name__} violates contract: capabilities() must return AdapterCapabilities"
        )

    if adapter.is_connected():
        raise ContractViolation(
            f"{type(adapter).__name__} violates contract: adapter must start in disconnected state"
        )

    try:
        await verify_adapter_contract(adapter, **kwargs)
    except ContractViolation as violation:
        raise ContractViolation(
            f"{type(adapter).__name__} violates the adapter contract: {violation}"
        ) from violation

    if adapter.is_connected():
        raise ContractViolation(
            f"{type(adapter).__name__} violates contract: adapter did not disconnect cleanly after test"
        )
