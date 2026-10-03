"""The shared suite must actually run, and must actually fail a broken adapter.

The reference implementation is :class:`honba.adapters.testing.FakeAdapter`. If the wrapper
cannot certify it, the suite is broken for every adapter in this repo.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from honba.adapters import AdapterCapabilities
from honba.adapters.boundary import BoundaryViolation, find_boundary_violations
from honba.adapters.testing import FakeAdapter

from honba_adapters_shared import ContractViolation, assert_adapter_contract


class _RenamedAdapter(FakeAdapter):
    """A facade whose descriptor disagrees with its registry name."""

    def capabilities(self) -> AdapterCapabilities:
        return replace(super().capabilities(), name="not-the-registry-name")


async def test_reference_implementation_passes_the_contract() -> None:
    await assert_adapter_contract(FakeAdapter())


async def test_contract_failure_becomes_a_test_failure() -> None:
    with pytest.raises(ContractViolation):
        await assert_adapter_contract(_RenamedAdapter())


async def test_contract_failure_names_the_offending_adapter() -> None:
    with pytest.raises(ContractViolation, match="_RenamedAdapter"):
        await assert_adapter_contract(_RenamedAdapter())


def test_contract_suite_is_exported_where_an_adapter_will_look_for_it() -> None:
    import honba_adapters_shared

    assert honba_adapters_shared.assert_adapter_contract is assert_adapter_contract
    assert honba_adapters_shared.ContractViolation is ContractViolation
    assert honba_adapters_shared.find_boundary_violations is find_boundary_violations
    assert issubclass(honba_adapters_shared.BoundaryViolation, BoundaryViolation)
