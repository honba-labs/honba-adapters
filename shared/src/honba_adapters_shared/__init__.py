"""Shared infrastructure for the broker adapters in this repository.

``honba-adapters-shared`` is what every package here has in common:
- Contract suite an adapter must pass (:func:`assert_adapter_contract`)
- Boundary check that keeps broker wire types inside their adapter (:func:`find_boundary_violations`)
- Test fixture loader (:func:`load_fixture`, :func:`fixtures_dir`)
- Token bucket rate limiter (:class:`RateLimiter`, :class:`TokenBucket`, :func:`rate_limited`)
- RFC 6238 TOTP generator and token management (:class:`TOTP`, :class:`TokenManager`, :class:`TokenRecord`)
- Reconnecting WebSocket transport and sequence tracker (:class:`ReconnectingWebSocketClient`, :class:`SequenceTracker`)
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

from honba_adapters_shared.auth import (
    TOTP,
    FileTokenStore,
    MemoryTokenStore,
    TokenManager,
    TokenRecord,
    TokenStore,
    generate_totp,
    verify_totp,
)
from honba_adapters_shared.contract import assert_adapter_contract
from honba_adapters_shared.errors import (
    AuthenticationError,
    RateLimitExceeded,
    SequenceGapError,
    TokenExpiredError,
    WebSocketError,
)
from honba_adapters_shared.fixtures import fixtures_dir, load_fixture
from honba_adapters_shared.rate_limit import (
    RateLimit,
    RateLimiter,
    TokenBucket,
    rate_limited,
)
from honba_adapters_shared.websocket import (
    ReconnectingWebSocketClient,
    SequenceCheckResult,
    SequenceTracker,
    WebSocketState,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "TOTP",
    "Adapter",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterFatalError",
    "AdapterNotFound",
    "AdapterRegistry",
    "AuthenticationError",
    "BoundaryViolation",
    "Capability",
    "CapabilityError",
    "ContractViolation",
    "ExecutionAdapter",
    "FileTokenStore",
    "MarketDataAdapter",
    "MemoryTokenStore",
    "RateLimit",
    "RateLimitExceeded",
    "RateLimiter",
    "ReconnectingWebSocketClient",
    "SequenceCheckResult",
    "SequenceGapError",
    "SequenceTracker",
    "SessionError",
    "SessionExpired",
    "TokenBucket",
    "TokenExpiredError",
    "TokenManager",
    "TokenRecord",
    "TokenStore",
    "WebSocketError",
    "WebSocketState",
    "assert_adapter_contract",
    "available_adapters",
    "capability_for_method",
    "default_registry",
    "find_boundary_violations",
    "fixtures_dir",
    "format_violations",
    "generate_totp",
    "load_fixture",
    "rate_limited",
    "register_adapter",
    "resolve_adapter",
    "verify_adapter_contract",
    "verify_totp",
]
