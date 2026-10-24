"""Mapping tables must cover everything the adapter declares (and stay aliased)."""

from __future__ import annotations

import pytest
from honba.adapters import AdapterCapabilities
from honba.domain.order import OrderType, TimeInForce

from honba_adapters.common.maps import MappingCoverageError, assert_covers
from honba_adapters.zerodha import ZerodhaAdapter, ZerodhaConfig, constants


def _caps() -> AdapterCapabilities:
    return ZerodhaAdapter(ZerodhaConfig.paper()).capabilities()


def test_declared_capabilities_are_mapped() -> None:
    assert_covers(
        _caps(),
        order_types=constants.ZERODHA_ORDER_TYPE_WIRE,
        products=constants.ZERODHA_PRODUCT_WIRE,
        tif=constants.ZERODHA_TIF_WIRE,
    )


def test_legacy_names_alias_the_wire_maps() -> None:
    assert constants.ZERODHA_ORDER_TYPE_MAP is constants.ZERODHA_ORDER_TYPE_WIRE.forward
    assert constants.ZERODHA_REVERSE_ORDER_TYPE_MAP is constants.ZERODHA_ORDER_TYPE_WIRE.reverse
    assert constants.ZERODHA_PRODUCT_MAP is constants.ZERODHA_PRODUCT_WIRE.forward
    assert constants.ZERODHA_REVERSE_PRODUCT_MAP is constants.ZERODHA_PRODUCT_WIRE.reverse
    assert constants.ZERODHA_TIF_MAP is constants.ZERODHA_TIF_WIRE.forward
    assert constants.ZERODHA_SIDE_MAP is constants.ZERODHA_SIDE_WIRE.forward


def test_trailing_stop_is_not_declared_or_mapped() -> None:
    assert OrderType.TRAILING_STOP not in _caps().order_types
    assert OrderType.TRAILING_STOP not in constants.ZERODHA_ORDER_TYPE_WIRE.forward


def test_adding_a_capability_without_a_mapping_fails_clearly() -> None:
    from dataclasses import replace

    caps = replace(
        _caps(),
        order_types=_caps().order_types | {OrderType.TRAILING_STOP},
        time_in_force=_caps().time_in_force | {TimeInForce.GTC},
    )
    with pytest.raises(MappingCoverageError, match="trailing_stop") as exc:
        assert_covers(
            caps, order_types=constants.ZERODHA_ORDER_TYPE_WIRE, tif=constants.ZERODHA_TIF_WIRE
        )
    assert "gtc" in str(exc.value)
    assert "zerodha" in str(exc.value)
