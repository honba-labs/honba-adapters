"""Bidirectional wire mapping tests for Angel One SmartAPI 2.0."""

from __future__ import annotations

from honba.domain.order import OrderStatus

from honba_adapters.angelone.constants import (
    ANGELONE_ORDER_TYPE_MAP,
    ANGELONE_ORDER_TYPE_WIRE,
    ANGELONE_PRODUCT_MAP,
    ANGELONE_PRODUCT_WIRE,
    ANGELONE_REVERSE_ORDER_TYPE_MAP,
    ANGELONE_REVERSE_PRODUCT_MAP,
    ANGELONE_REVERSE_SIDE_MAP,
    ANGELONE_REVERSE_TIF_MAP,
    ANGELONE_SIDE_MAP,
    ANGELONE_SIDE_WIRE,
    ANGELONE_STATUS_MAP,
    ANGELONE_TIF_MAP,
    ANGELONE_TIF_WIRE,
)


class TestAngelOneMappings:
    def test_order_type_mappings(self) -> None:
        for ot, wire in ANGELONE_ORDER_TYPE_MAP.items():
            assert ANGELONE_ORDER_TYPE_WIRE.forward[ot] == wire
            assert ANGELONE_REVERSE_ORDER_TYPE_MAP[wire] == ot

    def test_product_mappings(self) -> None:
        for prod, wire in ANGELONE_PRODUCT_MAP.items():
            assert ANGELONE_PRODUCT_WIRE.forward[prod] == wire
            assert ANGELONE_REVERSE_PRODUCT_MAP[wire] == prod

    def test_side_mappings(self) -> None:
        for side, wire in ANGELONE_SIDE_MAP.items():
            assert ANGELONE_SIDE_WIRE.forward[side] == wire
            assert ANGELONE_REVERSE_SIDE_MAP[wire] == side

    def test_tif_mappings(self) -> None:
        for tif, wire in ANGELONE_TIF_MAP.items():
            assert ANGELONE_TIF_WIRE.forward[tif] == wire
            assert ANGELONE_REVERSE_TIF_MAP[wire] == tif

    def test_status_mappings(self) -> None:
        assert ANGELONE_STATUS_MAP["COMPLETE"] is OrderStatus.FILLED
        assert ANGELONE_STATUS_MAP["REJECTED"] is OrderStatus.REJECTED
        assert ANGELONE_STATUS_MAP["CANCELLED"] is OrderStatus.CANCELLED
        assert ANGELONE_STATUS_MAP["OPEN"] is OrderStatus.ACCEPTED
