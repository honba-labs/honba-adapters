"""Tests for the one-dict wire mapping helper and the capability coverage guard."""

from __future__ import annotations

import pytest
from honba.adapters import AdapterCapabilities
from honba.adapters.models import Product
from honba.domain.order import OrderType, TimeInForce

from honba_adapters_shared.maps import (
    AmbiguousMappingError,
    MappingCoverageError,
    WireMap,
    assert_covers,
)

ORDER_TYPES = WireMap(
    {OrderType.MARKET: "MARKET", OrderType.LIMIT: "LIMIT", OrderType.STOP_LIMIT: "SL"},
    name="order_type",
)


def test_reverse_is_derived_from_forward() -> None:
    assert ORDER_TYPES.forward[OrderType.STOP_LIMIT] == "SL"
    assert ORDER_TYPES.reverse == {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
        "SL": OrderType.STOP_LIMIT,
    }


def test_duplicate_wire_value_is_ambiguous() -> None:
    with pytest.raises(AmbiguousMappingError, match="'MIS'"):
        WireMap({Product.INTRADAY: "MIS", Product.DELIVERY: "MIS"}, name="product")


def test_extra_inbound_aliases_extend_reverse_only() -> None:
    m = WireMap({Product.INTRADAY: "INTRADAY"}, extra_inbound={"CO": Product.INTRADAY})
    assert m.reverse["CO"] is Product.INTRADAY
    assert "CO" not in m.forward.values()


def test_extra_inbound_colliding_with_forward_is_ambiguous() -> None:
    with pytest.raises(AmbiguousMappingError, match="CNC"):
        WireMap({Product.DELIVERY: "CNC"}, extra_inbound={"CNC": Product.CARRY})


def _caps(**kw: object) -> AdapterCapabilities:
    return AdapterCapabilities(name="fake", **kw)  # type: ignore[arg-type]


def test_assert_covers_passes_when_all_mapped() -> None:
    caps = _caps(order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}))
    assert_covers(caps, order_types=ORDER_TYPES)


def test_assert_covers_lists_every_uncovered_member() -> None:
    caps = _caps(
        order_types=frozenset({OrderType.MARKET, OrderType.TRAILING_STOP, OrderType.STOP_MARKET}),
        time_in_force=frozenset({TimeInForce.DAY, TimeInForce.GTC}),
    )
    tif = WireMap({TimeInForce.DAY: "DAY"}, name="tif")
    with pytest.raises(MappingCoverageError) as exc:
        assert_covers(caps, order_types=ORDER_TYPES, tif=tif)
    msg = str(exc.value)
    assert "fake" in msg
    assert OrderType.TRAILING_STOP.value in msg
    assert OrderType.STOP_MARKET.value in msg
    assert TimeInForce.GTC.value in msg
    assert OrderType.MARKET.value not in msg.replace(OrderType.STOP_MARKET.value, "")


def test_assert_covers_checks_products() -> None:
    caps = _caps(products=frozenset({Product.DELIVERY, Product.CARRY}))
    prod = WireMap({Product.DELIVERY: "CNC"}, name="product")
    with pytest.raises(MappingCoverageError, match=Product.CARRY.value):
        assert_covers(caps, products=prod)
