"""Mapping tables must cover everything the adapter declares (and stay aliased)."""

from __future__ import annotations

import pytest
from honba.adapters import AdapterCapabilities
from honba.domain.order import OrderType, TimeInForce
from honba_adapters_shared.maps import MappingCoverageError, assert_covers

from honba_dhan import DhanAdapter, DhanConfig, constants


def _caps() -> AdapterCapabilities:
    return DhanAdapter(DhanConfig.paper()).capabilities()


def test_declared_capabilities_are_mapped() -> None:
    assert_covers(
        _caps(),
        order_types=constants.DHAN_ORDER_TYPE_WIRE,
        products=constants.DHAN_PRODUCT_WIRE,
        tif=constants.DHAN_TIF_WIRE,
    )


def test_legacy_names_alias_the_wire_maps() -> None:
    assert constants.HONBA_ORDER_TYPE_TO_DHAN is constants.DHAN_ORDER_TYPE_WIRE.forward
    assert constants.DHAN_ORDER_TYPE_TO_HONBA is constants.DHAN_ORDER_TYPE_WIRE.reverse
    assert constants.HONBA_PRODUCT_TO_DHAN is constants.DHAN_PRODUCT_WIRE.forward
    assert constants.DHAN_PRODUCT_TO_HONBA["MTF"].value == "carry"


def test_trailing_stop_is_not_declared_or_mapped() -> None:
    assert OrderType.TRAILING_STOP not in _caps().order_types
    assert OrderType.TRAILING_STOP not in constants.DHAN_ORDER_TYPE_WIRE.forward


def test_adding_a_capability_without_a_mapping_fails_clearly() -> None:
    from dataclasses import replace

    caps = replace(
        _caps(),
        order_types=_caps().order_types | {OrderType.TRAILING_STOP},
        time_in_force=_caps().time_in_force | {TimeInForce.GTC},
    )
    with pytest.raises(MappingCoverageError, match="trailing_stop") as exc:
        assert_covers(caps, order_types=constants.DHAN_ORDER_TYPE_WIRE, tif=constants.DHAN_TIF_WIRE)
    assert "gtc" in str(exc.value)
    assert "dhan" in str(exc.value)


def test_order_payload_validity_follows_time_in_force() -> None:
    from honba.adapters.models import Product
    from honba.domain.instrument import InstrumentId
    from honba.domain.order import OrderIntent, OrderSide

    from honba_dhan.parsing import build_order_payload

    def validity(tif: TimeInForce) -> str:
        intent = OrderIntent(
            instrument_id=InstrumentId("RELIANCE", "NSE"),
            side=OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.MARKET,
            time_in_force=tif,
        )
        payload = build_order_payload(intent, Product.DELIVERY, None, "1333", "NSE_EQ")
        return str(payload["validity"])

    assert validity(TimeInForce.DAY) == "DAY"
    assert validity(TimeInForce.IOC) == "IOC"


async def test_place_order_rejects_unmapped_time_in_force_preflight() -> None:
    from honba.adapters import CapabilityError
    from honba.adapters.models import Product
    from honba.domain.instrument import InstrumentId
    from honba.domain.order import OrderIntent, OrderSide

    adapter = DhanAdapter(DhanConfig.paper())
    await adapter.connect()
    try:
        intent = OrderIntent(
            instrument_id=InstrumentId("RELIANCE", "NSE"),
            side=OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
        )
        with pytest.raises(CapabilityError, match="gtc"):
            await adapter.place_order(intent, product=Product.DELIVERY)
    finally:
        await adapter.disconnect()
