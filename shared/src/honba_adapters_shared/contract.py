"""The pytest-facing wrapper around the core adapter contract suite.

The core suite in :mod:`honba.adapters.contract` is pytest-free on purpose: it is also run by
the engine's own checks and by scripts, so it must not depend on a test runner. This module is
the thin pytest-shaped seam over it, so an adapter's suite is one line rather than an await
plus a try/except.

It stays pytest-*agnostic* too: ``ContractViolation`` is an ``AssertionError``, which every
runner already reports as a failure, and nothing here imports pytest. That keeps the helper
usable from ``pytest``, from ``unittest.IsolatedAsyncioTestCase`` and from a plain
``asyncio.run`` script.
"""

from __future__ import annotations

from typing import Any

from honba.adapters.base import Adapter
from honba.adapters.contract import ContractViolation, verify_adapter_contract

__all__ = ["ContractViolation", "assert_adapter_contract"]


async def assert_adapter_contract(adapter: Adapter, **kwargs: Any) -> None:
    """Assert ``adapter`` satisfies the whole contract, or raise :class:`ContractViolation`.

    ``kwargs`` are forwarded to :func:`honba.adapters.contract.verify_adapter_contract`
    (``probe_quantity``, ``disconnect``). A violation is re-raised with the adapter's class name
    prepended, so a failure in a suite that drives several adapters says which one broke, and
    the original violation is chained as the cause.

    An adapter's own ``tests/test_contract.py`` is then exactly this::

        from honba_dhan import DhanAdapter, DhanConfig
        from honba_adapters_shared import assert_adapter_contract

        async def test_dhan_satisfies_the_contract() -> None:
            await assert_adapter_contract(DhanAdapter(DhanConfig.paper()))

    With ``asyncio_mode = "auto"`` in the adapter's pytest config (what the core repo uses) no
    marker is needed; otherwise add ``@pytest.mark.asyncio``. ``pytest-asyncio`` is a test
    dependency of the adapter, not of this package: the wrapper itself is plain ``async``.
    """
    try:
        await verify_adapter_contract(adapter, **kwargs)
    except ContractViolation as violation:
        raise ContractViolation(
            f"{type(adapter).__name__} violates the adapter contract: {violation}"
        ) from violation
