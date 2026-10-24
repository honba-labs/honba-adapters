"""Backward-compatibility shim forwarding to honba_adapters.common."""

from __future__ import annotations

import sys

import honba_adapters.common as _mod
from honba_adapters.common import (
    BoundaryViolation,
    ContractViolation,
    assert_adapter_contract,
    find_boundary_violations,
)

__all__ = [
    "BoundaryViolation",
    "ContractViolation",
    "assert_adapter_contract",
    "find_boundary_violations",
]

sys.modules[__name__] = _mod
