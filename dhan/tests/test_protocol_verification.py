"""Pins protocol values verified against Dhan's published docs (see README verification table)."""

from __future__ import annotations

from honba_dhan import DhanConfig
from honba_dhan.http import DhanHttpClient


def test_quote_endpoint_pool_is_limited_to_one_request_per_second() -> None:
    # https://dhanhq.co/docs/v2/ rate limits: Data APIs 5/s, Quote APIs 1/s.
    http = DhanHttpClient(DhanConfig(client_id="1", access_token="t"))
    assert http.limiter.bucket("quote").limit.rate_per_second == 1.0
    assert http.limiter.bucket("data").limit.rate_per_second == 5.0
