"""Shared test fixtures: the read-only guard that keeps live tests from placing orders."""

from __future__ import annotations

from typing import Any

import pytest

# Only these (method, path-prefix) pairs may reach the network from a live test. Dhan's
# marketfeed/charts/margincalculator POSTs are read-only queries; everything else is denied.
_ALLOWED: dict[str, tuple[str, ...]] = {
    "GET": ("/fundlimit", "/orders", "/trades", "/positions", "/holdings"),
    "POST": (
        "/marketfeed/",
        "/charts/historical",
        "/charts/intraday",
        "/margincalculator",
    ),
}
_ORDER_METHODS = ("place_order", "modify_order", "cancel_order", "cancel_all")


def _refuse(what: str) -> Any:
    raise AssertionError(f"read-only guard: refusing {what}")


def _install_readonly_guard(adapter: Any) -> None:
    http = adapter._http
    real_request = http.request

    async def guarded_request(method: str, path: str, **kwargs: Any) -> Any:
        prefixes = _ALLOWED.get(method.upper(), ())
        if not any(path == p or path.startswith(p) for p in prefixes):
            _refuse(f"{method.upper()} {path}")
        return await real_request(method, path, **kwargs)

    http.request = guarded_request
    for name in _ORDER_METHODS:

        async def blocked(*_a: Any, _name: str = name, **_kw: Any) -> Any:
            _refuse(f"adapter.{_name}()")

        setattr(adapter, name, blocked)


@pytest.fixture
def install_readonly_guard():
    return _install_readonly_guard
