"""Property-based tests for exact Decimal parsing and zero precision loss (Hypothesis)."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from honba_adapters.angelone.parsing import decimal_str, format_decimal, whole_quantity


class TestDecimalPrecisionInvariants:
    @given(st.decimals(min_value=Decimal(-1000000000), max_value=Decimal(1000000000), places=8))
    def test_decimal_str_preserves_exact_value(self, d: Decimal) -> None:
        """Any valid Decimal formatted as string parses back to the exact Decimal."""
        str_val = str(d)
        parsed = decimal_str(str_val)
        assert parsed == d

    @given(st.integers(min_value=1, max_value=1_000_000))
    def test_whole_quantity_integers(self, n: int) -> None:
        """Whole integer quantities round-trip exactly."""
        assert whole_quantity(n) == n
        assert whole_quantity(float(n)) == n
        assert whole_quantity(str(n)) == n

    @given(
        st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("999999.99"),
            places=2,
        )
    )
    def test_format_decimal_roundtrip_two_places(self, d: Decimal) -> None:
        """Serializing and deserializing financial decimals with scale 2 preserves exact equality."""
        formatted = format_decimal(d, scale=2)
        parsed = decimal_str(formatted)
        assert parsed == d
        # Formatting string representation must exactly match expected scale
        assert len(formatted.split(".")[1]) == 2

    @given(
        st.decimals(
            min_value=Decimal("0.00000001"),
            max_value=Decimal("99999.99999999"),
            places=8,
        )
    )
    def test_high_precision_crypto_currency_scale(self, d: Decimal) -> None:
        """High-precision currencies up to 8 decimal places preserve exact equality."""
        formatted = format_decimal(d, scale=8)
        parsed = decimal_str(formatted)
        assert parsed == d
        assert len(formatted.split(".")[1]) == 8
