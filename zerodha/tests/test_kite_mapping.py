"""Unit tests for Kite vocabulary mapping ported from ``honba-broker-zerodha`` (mapping.rs)."""

from __future__ import annotations

from typing import ClassVar

import pytest
from honba.adapters.models import Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderStatus, TimeInForce

from honba_zerodha.constants import ZERODHA_TIF_MAP
from honba_zerodha.parsing import (
    build_order_payload,
    kite_tag,
    parse_kite_timestamp,
    parse_order,
    parse_trade,
    whole_quantity,
)


class TestTimestamp:
    def test_epoch_in_ist(self) -> None:
        assert parse_kite_timestamp("1970-01-01 05:30:00") == 0
        assert parse_kite_timestamp("1970-01-01 05:30:01") == 1_000_000_000

    def test_leap_day(self) -> None:
        # 2024-02-29 09:15:00 IST == 03:45:00 UTC
        assert parse_kite_timestamp("2024-02-29 09:15:00") == 1_709_178_300 * 1_000_000_000
        assert (
            parse_kite_timestamp("2024-03-01 05:30:00") == (1_709_164_800 + 86_400) * 1_000_000_000
        )

    def test_century_leap_year_2000(self) -> None:
        assert parse_kite_timestamp("2000-03-01 00:00:00") == 951_849_000 * 1_000_000_000

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "garbage",
            "2024-02-29T09:15:00",
            "2023-02-29 09:15:00",
            "2100-02-29 09:15:00",
            "2024-13-01 09:15:00",
            "2024-00-10 09:15:00",
            "2024-04-31 09:15:00",
            "2024-04-30 24:00:00",
            "2024-04-30 12:60:00",
            "2024-04-30 12:00:60",
            "1970-01-01 00:00:00",
            "2024-4-30 12:00:00",
            "+024-04-30 12:00:00",
        ],
    )
    def test_rejects_invalid_input(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_kite_timestamp(bad)


class TestTag:
    def test_alphanumeric_truncated_and_deterministic(self) -> None:
        assert kite_tag("hb-123_abc") == "hb123abc"
        long = "a1" * 20
        assert kite_tag(long) == "a1" * 10
        assert len(kite_tag(long)) == 20
        assert kite_tag(long) == kite_tag(long)
        assert kite_tag("---") == "HONBA"
        assert kite_tag("") == "HONBA"
        assert kite_tag("é-ü") == "HONBA"


class TestTimeInForce:
    def test_only_day_and_ioc(self) -> None:
        assert ZERODHA_TIF_MAP == {TimeInForce.DAY: "DAY", TimeInForce.IOC: "IOC"}

    def test_payload_uses_intent_validity(self) -> None:
        inst = InstrumentId("INFY", "NSE")
        for tif, wire in ((TimeInForce.DAY, "DAY"), (TimeInForce.IOC, "IOC")):
            intent = OrderIntent(inst, OrderSide.BUY, 10.0, time_in_force=tif)
            payload = build_order_payload(
                intent=intent,
                product=Product.INTRADAY,
                client_order_id=None,
                tradingsymbol="INFY",
                exchange="NSE",
            )
            assert payload["validity"] == wire
            assert payload["product"] == "MIS"
            assert "tag" not in payload


class TestWholeQuantity:
    def test_accepts_positive_whole_numbers(self) -> None:
        assert whole_quantity(10.0) == 10

    @pytest.mark.parametrize("bad", [0.0, -1.0, 2.5, float("nan"), float("inf")])
    def test_rejects_others(self, bad: float) -> None:
        with pytest.raises(ValueError):
            whole_quantity(bad)

    def test_payload_rejects_fractional_quantity(self) -> None:
        intent = OrderIntent(InstrumentId("INFY", "NSE"), OrderSide.BUY, 2.5)
        with pytest.raises(ValueError):
            build_order_payload(
                intent=intent,
                product=Product.DELIVERY,
                client_order_id=None,
                tradingsymbol="INFY",
                exchange="NSE",
            )


def _rec(status: str, filled: float | None, qty: float | None) -> dict[str, object]:
    item: dict[str, object] = {
        "order_id": "1",
        "tradingsymbol": "INFY",
        "exchange": "NSE",
        "status": status,
        "average_price": 1500.0,
    }
    if filled is not None:
        item["filled_quantity"] = filled
    if qty is not None:
        item["quantity"] = qty
    return item


class TestOrderStatus:
    def test_partial_fill_is_open_with_some_filled(self) -> None:
        assert parse_order(_rec("OPEN", 3, 10)).status is OrderStatus.PARTIALLY_FILLED
        assert parse_order(_rec("OPEN", 0, 10)).status is OrderStatus.ACCEPTED
        assert parse_order(_rec("OPEN", 10, 10)).status is OrderStatus.ACCEPTED
        assert parse_order(_rec("OPEN", None, 10)).status is OrderStatus.ACCEPTED
        assert parse_order(_rec("COMPLETE", 10, 10)).status is OrderStatus.FILLED

    def test_status_vocabulary(self) -> None:
        assert parse_order(_rec("CANCELLED", 0, 10)).status is OrderStatus.CANCELLED
        assert parse_order(_rec("REJECTED", 0, 10)).status is OrderStatus.REJECTED
        assert parse_order(_rec("TRIGGER PENDING", 0, 10)).status is OrderStatus.ACCEPTED

    def test_event_timestamp_from_exchange_update(self) -> None:
        item = _rec("OPEN", 0, 10)
        item["order_timestamp"] = "2024-02-29 09:15:00"
        item["exchange_update_timestamp"] = "2024-02-29 09:15:01"
        assert parse_order(item).ts_event == (1_709_178_300 + 1) * 1_000_000_000
        del item["exchange_update_timestamp"]
        assert parse_order(item).ts_event == 1_709_178_300 * 1_000_000_000


class TestTradeValidation:
    base: ClassVar[dict[str, object]] = {
        "trade_id": "T1",
        "order_id": "1",
        "tradingsymbol": "INFY",
        "exchange": "NSE",
        "transaction_type": "BUY",
        "quantity": 4,
        "average_price": 1501.0,
        "fill_timestamp": "2024-01-02 09:15:30",
    }

    def test_fill_timestamp_becomes_trade_ts(self) -> None:
        t = parse_trade(dict(self.base))
        assert t.ts == parse_kite_timestamp("2024-01-02 09:15:30")

    @pytest.mark.parametrize(
        "patch",
        [
            {"quantity": 0},
            {"quantity": -1},
            {"average_price": 0.0},
            {"average_price": float("nan")},
            {"fill_timestamp": "garbage"},
        ],
    )
    def test_phantom_rows_are_rejected(self, patch: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            parse_trade({**self.base, **patch})
