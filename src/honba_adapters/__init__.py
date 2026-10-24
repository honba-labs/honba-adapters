"""Production broker SDK and adapter implementations for the Honba platform.

Standardized adapters implementing the canonical ADR 0010 contract (lifecycle,
market data, execution) with zero-float Decimal parsing, boundary isolation,
and recorded golden response replay.
"""

from __future__ import annotations

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
    default_registry,
    register_adapter,
    resolve_adapter,
)
from honba.adapters.contract import ContractViolation
from honba.adapters.registry import ENTRY_POINT_GROUP, AdapterRegistry

from honba_adapters.angelone import AngelOneAdapter, AngelOneConfig
from honba_adapters.common import (
    TOTP,
    RateLimiter,
    ReconnectingWebSocketClient,
    WireMap,
    assert_adapter_contract,
    assert_covers,
    load_fixture,
    recorded_transport,
)
from honba_adapters.dhan import DhanAdapter, DhanConfig
from honba_adapters.zerodha import ZerodhaAdapter, ZerodhaConfig

__version__ = "0.1.0"

__all__ = [
    "ENTRY_POINT_GROUP",
    "TOTP",
    "Adapter",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterNotFound",
    "AdapterRegistry",
    "AngelOneAdapter",
    "AngelOneConfig",
    "Capability",
    "CapabilityError",
    "ContractViolation",
    "DhanAdapter",
    "DhanConfig",
    "ExecutionAdapter",
    "MarketDataAdapter",
    "RateLimiter",
    "ReconnectingWebSocketClient",
    "SessionError",
    "WireMap",
    "ZerodhaAdapter",
    "ZerodhaConfig",
    "__version__",
    "assert_adapter_contract",
    "assert_covers",
    "available_adapters",
    "default_registry",
    "load_fixture",
    "recorded_transport",
    "register_adapter",
    "resolve_adapter",
]
