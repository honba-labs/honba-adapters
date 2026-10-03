"""Shared test infrastructure for the broker adapters in this repository.

``honba-adapters-shared`` is what every package here has in common. Today that is test
infrastructure: the contract suite an adapter must pass, the boundary check that keeps broker
wire types inside their adapter, and the reader for recorded broker responses. Auth, rate
limiting and websocket helpers are declared here and arrive with their stories (E1-S5, E1-S6).

The contract itself is not defined here. It lives in the core ``honba`` package, in
:mod:`honba.adapters`, and this package re-exports it so an adapter's suite has a single
import that stays stable while the core moves its modules around::

    from honba_adapters_shared import assert_adapter_contract

    async def test_dhan_satisfies_the_contract() -> None:
        await assert_adapter_contract(DhanAdapter(DhanConfig.paper()))

Install it with the broker packages: ``pip install -e shared[dev]`` for this repo's own tests.
"""

from honba.adapters import (
    Adapter,
    AdapterCapabilities,
    AdapterError,
    AdapterNotFound,
    Capability,
    CapabilityError,
    ExecutionAdapter,
    MarketDataAdapter,
    SessionError,
    available_adapters,
    capability_for_method,
    default_registry,
    register_adapter,
    resolve_adapter,
)
from honba.adapters.boundary import (
    BoundaryViolation,
    find_boundary_violations,
    format_violations,
)
from honba.adapters.contract import ContractViolation, verify_adapter_contract
from honba.adapters.errors import AdapterFatalError, SessionExpired
from honba.adapters.registry import ENTRY_POINT_GROUP, AdapterRegistry

from honba_adapters_shared.contract import assert_adapter_contract
from honba_adapters_shared.fixtures import fixtures_dir, load_fixture

__all__ = [
    "ENTRY_POINT_GROUP",
    "Adapter",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterFatalError",
    "AdapterNotFound",
    "AdapterRegistry",
    "BoundaryViolation",
    "Capability",
    "CapabilityError",
    "ContractViolation",
    "ExecutionAdapter",
    "MarketDataAdapter",
    "SessionError",
    "SessionExpired",
    "assert_adapter_contract",
    "available_adapters",
    "capability_for_method",
    "default_registry",
    "find_boundary_violations",
    "fixtures_dir",
    "format_violations",
    "load_fixture",
    "register_adapter",
    "resolve_adapter",
    "verify_adapter_contract",
]
