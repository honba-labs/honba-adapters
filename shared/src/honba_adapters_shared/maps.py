"""One-dict wire mappings and the capability coverage guard.

An adapter declares what it supports in :class:`AdapterCapabilities` and translates honba
enums to broker strings. Those are two sources of truth: declaring a capability without a
mapping entry only fails (``KeyError``) when someone places such an order. :class:`WireMap`
derives the reverse table from the forward one, so the two cannot drift, and
:func:`assert_covers` checks the declared capabilities against the tables up front.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any, Generic, TypeVar

from honba.adapters import AdapterCapabilities

__all__ = ["AmbiguousMappingError", "MappingCoverageError", "WireMap", "assert_covers"]

K = TypeVar("K", bound=Enum)


class AmbiguousMappingError(ValueError):
    """Two honba members (or an alias) claim the same wire value on the inbound side."""


class MappingCoverageError(AssertionError):
    """An adapter declares capability members that have no wire mapping."""


class WireMap(Generic[K]):
    """Forward (honba -> wire) and reverse (wire -> honba) tables from one dict.

    ``extra_inbound`` adds wire values the broker may *return* but that are never sent
    (e.g. Dhan's ``MTF``/``CO``/``BO`` products). They must not collide with a derived entry.
    """

    def __init__(
        self,
        forward: Mapping[K, str],
        *,
        extra_inbound: Mapping[str, K] | None = None,
        name: str = "mapping",
    ) -> None:
        self.name = name
        self.forward: dict[K, str] = dict(forward)
        reverse: dict[str, K] = {}
        for member, wire in self.forward.items():
            if wire in reverse:
                raise AmbiguousMappingError(
                    f"{name}: wire value {wire!r} maps back to both "
                    f"{reverse[wire].value!r} and {member.value!r}"
                )
            reverse[wire] = member
        for wire, member in (extra_inbound or {}).items():
            if wire in reverse:
                raise AmbiguousMappingError(
                    f"{name}: inbound alias {wire!r} collides with {reverse[wire].value!r}"
                )
            reverse[wire] = member
        self.reverse: dict[str, K] = reverse

    def uncovered(self, declared: Iterable[K]) -> list[K]:
        """Declared members with no forward entry, in a stable (value-sorted) order."""
        return sorted((m for m in declared if m not in self.forward), key=lambda m: m.value)


def assert_covers(
    capabilities: AdapterCapabilities,
    *,
    order_types: WireMap[Any] | None = None,
    products: WireMap[Any] | None = None,
    tif: WireMap[Any] | None = None,
) -> None:
    """Raise :class:`MappingCoverageError` listing every declared member lacking a mapping."""
    checks = (
        ("order_types", capabilities.order_types, order_types),
        ("products", capabilities.products, products),
        ("time_in_force", capabilities.time_in_force, tif),
    )
    problems = [
        f"{label}: {', '.join(m.value for m in table.uncovered(declared))}"
        for label, declared, table in checks
        if table is not None and table.uncovered(declared)
    ]
    if problems:
        raise MappingCoverageError(
            f"adapter {capabilities.name} declares capabilities without a wire mapping "
            f"(would KeyError at order time) -- {'; '.join(problems)}"
        )
