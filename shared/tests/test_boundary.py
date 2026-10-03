"""The boundary rule, enforced over this repo's own source tree.

One rule: broker wire types never appear outside an adapter implementation. Inside
``<broker>/src/honba_<broker>`` a broker SDK is the point; anywhere else it is a leak.
"""

from __future__ import annotations

from pathlib import Path

from honba.adapters.boundary import DEFAULT_FORBIDDEN

from honba_adapters_shared import find_boundary_violations, format_violations

REPO_ROOT = Path(__file__).resolve().parents[2]
BROKER_PACKAGES = (
    "angelone",
    "dhan",
    "fyers",
    "iifl",
    "kotak",
    "motilal",
    "upstox",
    "zerodha",
)
#: Inside this repository importing the shared package is the whole point of it, so it is not
#: forbidden here — unlike the seven broker packages, which stay forbidden everywhere outside
#: their own adapter root. A broker SDK stays forbidden in all of ``src/``, which is what keeps
#: the shared package broker-neutral instead of exempting it wholesale.
FORBIDDEN = frozenset(DEFAULT_FORBIDDEN - {"honba_adapters_shared"})
ADAPTER_ROOTS = tuple(REPO_ROOT / broker / "src" / f"honba_{broker}" for broker in BROKER_PACKAGES)


def _src_roots() -> tuple[Path, ...]:
    roots = [REPO_ROOT / "shared" / "src"]
    roots.extend(REPO_ROOT / broker / "src" for broker in BROKER_PACKAGES)
    return tuple(roots)


def test_every_adapter_package_directory_exists() -> None:
    missing = [str(root) for root in ADAPTER_ROOTS if not root.is_dir()]
    assert not missing, f"adapter roots that no longer exist: {missing}"


def test_adapter_sources_import_no_broker_sdk() -> None:
    violations = [
        violation
        for root in _src_roots()
        for violation in find_boundary_violations(
            root, forbidden=FORBIDDEN, adapter_roots=ADAPTER_ROOTS
        )
    ]
    assert not violations, (
        f"broker wire types escaped their adapter:\n{format_violations(violations)}"
    )


def test_the_shared_package_stays_broker_neutral() -> None:
    assert "dhanhq" in FORBIDDEN
    assert "honba_dhan" in FORBIDDEN
    assert "honba_adapters_shared" not in FORBIDDEN


def test_an_sdk_import_outside_an_adapter_is_a_violation(tmp_path: Path) -> None:
    package = tmp_path / "honba_reporting"
    package.mkdir()
    (package / "exporter.py").write_text(
        "from dhanhq import DhanClient\nimport SmartApi\nfrom fyers_api import fyers\n",
        encoding="utf-8",
    )
    (package / "clean.py").write_text("from honba.domain.tick import QuoteTick\n", encoding="utf-8")

    violations = find_boundary_violations(package)

    assert len(violations) == 3, format_violations(violations)
    assert [v.module for v in violations] == ["dhanhq", "SmartApi", "fyers_api"]
    assert all("a broker SDK" in v.reason for v in violations)


def test_an_sdk_import_inside_an_adapter_is_allowed(tmp_path: Path) -> None:
    package = tmp_path / "honba_reporting"
    package.mkdir()
    (package / "exporter.py").write_text("from dhanhq import DhanClient\n", encoding="utf-8")

    assert find_boundary_violations(package, adapter_roots=(package,)) == []


def test_a_syntax_error_is_a_violation(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def oops(:\n", encoding="utf-8")

    violations = find_boundary_violations(tmp_path)

    assert len(violations) == 1, format_violations(violations)
    assert "syntax error" in violations[0].reason
