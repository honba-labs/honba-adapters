"""Property-based wire parsing fuzzing and crash immunity tests (Hypothesis).

Guarantees that randomized, incomplete, or malformed wire response payloads
never crash adapter deserializers with unhandled KeyError, IndexError, or TypeError.
"""

from __future__ import annotations

from typing import Any

from honba.domain.instrument import InstrumentId
from hypothesis import given
from hypothesis import strategies as st

from honba_adapters.angelone import parsing as angel_parsing
from honba_adapters.dhan import parsing as dhan_parsing
from honba_adapters.zerodha import parsing as zerodha_parsing

RELIANCE = InstrumentId("RELIANCE", "NSE")

# Strategy generating arbitrary JSON-like dictionaries
json_primitives = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text()
)
json_values = st.recursive(
    json_primitives,
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=10), children, max_size=5)
    ),
    max_leaves=15,
)
json_dicts = st.dictionaries(st.text(max_size=15), json_values, max_size=8)


class TestParsingInvariants:
    @given(json_dicts)
    def test_angelone_parse_funds_crash_immunity(self, payload: dict[str, Any]) -> None:
        """Arbitrary dict payloads never cause unhandled crashes in funds parser."""
        try:
            funds = angel_parsing.parse_funds(payload)
            assert funds.available_cash >= 0.0
        except ValueError:
            pass

    @given(json_dicts)
    def test_angelone_parse_position_crash_immunity(self, payload: dict[str, Any]) -> None:
        """Arbitrary dict payloads never cause unhandled crashes in position parser."""
        try:
            angel_parsing.parse_position(payload)
        except ValueError:
            pass

    @given(json_dicts)
    def test_angelone_parse_order_crash_immunity(self, payload: dict[str, Any]) -> None:
        """Arbitrary dict payloads never cause unhandled crashes in order parser."""
        try:
            angel_parsing.parse_order(payload)
        except ValueError:
            pass

    @given(json_dicts)
    def test_angelone_parse_trade_crash_immunity(self, payload: dict[str, Any]) -> None:
        """Arbitrary dict payloads never cause unhandled crashes in trade parser."""
        try:
            angel_parsing.parse_trade(payload)
        except ValueError:
            pass

    @given(json_dicts)
    def test_angelone_parse_holding_crash_immunity(self, payload: dict[str, Any]) -> None:
        """Arbitrary dict payloads never cause unhandled crashes in holding parser."""
        try:
            angel_parsing.parse_holding(payload)
        except ValueError:
            pass

    @given(json_dicts)
    def test_zerodha_parse_funds_crash_immunity(self, payload: dict[str, Any]) -> None:
        """Arbitrary dict payloads never cause unhandled crashes in Zerodha funds parser."""
        try:
            zerodha_parsing.parse_funds(payload)
        except ValueError:
            pass

    @given(json_dicts)
    def test_dhan_parse_funds_crash_immunity(self, payload: dict[str, Any]) -> None:
        """Arbitrary dict payloads never cause unhandled crashes in Dhan funds parser."""
        try:
            dhan_parsing.parse_funds(payload)
        except ValueError:
            pass
