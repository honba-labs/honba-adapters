"""Comprehensive boundary isolation and vendor leak prevention tests.

Proves that zero broker SDK wire types or third-party vendor libraries leak
beyond their respective adapter packages.
"""

from __future__ import annotations

from pathlib import Path

from honba.adapters.boundary import DEFAULT_FORBIDDEN

from honba_adapters.common import find_boundary_violations, format_violations

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
ADAPTERS_ROOT = SRC_ROOT / "honba_adapters"
BROKERS = ("dhan", "zerodha", "angelone")
ADAPTER_ROOTS = tuple(ADAPTERS_ROOT / broker for broker in BROKERS)
FORBIDDEN = frozenset(
    DEFAULT_FORBIDDEN - {"honba_adapters_shared", "honba_adapters", "honba_angelone"}
)


class TestBoundaryIsolation:
    def test_all_broker_packages_exist(self) -> None:
        for broker in BROKERS:
            adapter_dir = ADAPTERS_ROOT / broker
            assert adapter_dir.is_dir(), f"Expected adapter directory missing: {adapter_dir}"

    def test_zero_broker_sdk_leaks_in_adapters_root(self) -> None:
        violations = list(
            find_boundary_violations(
                ADAPTERS_ROOT,
                forbidden=FORBIDDEN,
                adapter_roots=ADAPTER_ROOTS,
            )
        )
        assert not violations, f"Boundary violations detected:\n{format_violations(violations)}"

    def test_common_package_has_no_broker_dependencies(self) -> None:
        common_dir = ADAPTERS_ROOT / "common"
        violations = list(
            find_boundary_violations(
                common_dir,
                forbidden=FORBIDDEN,
                adapter_roots=(),
            )
        )
        assert not violations, (
            f"Common infra has vendor violations:\n{format_violations(violations)}"
        )

    def test_backward_compatibility_shims_stay_clean(self) -> None:
        for shim in ("honba_dhan", "honba_zerodha", "honba_angelone", "honba_adapters_shared"):
            shim_dir = SRC_ROOT / shim
            if shim_dir.is_dir():
                violations = list(
                    find_boundary_violations(
                        shim_dir, forbidden=FORBIDDEN, adapter_roots=(shim_dir,)
                    )
                )
                assert not violations, (
                    f"Shim {shim} has violations:\n{format_violations(violations)}"
                )
