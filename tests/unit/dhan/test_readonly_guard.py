"""The live-test read-only guard must make order placement impossible (offline, mock transport)."""

from __future__ import annotations

import pytest

from honba_adapters.dhan import DhanAdapter, DhanConfig, make_dhan_mock_transport


@pytest.fixture
async def guarded(install_readonly_guard):
    adapter = DhanAdapter(DhanConfig.paper(), http_transport=make_dhan_mock_transport())
    await adapter.connect()
    install_readonly_guard(adapter)
    yield adapter
    await adapter.disconnect()


class TestReadOnlyGuard:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/orders"),
            ("put", "/orders/123"),
            ("delete", "/orders/123"),
            ("post", "/orders/slicing"),
            ("post", "/super/orders"),
            ("post", "/killswitch"),
            ("post", "/positions/convert"),
            ("get", "/some/unknown/path"),
        ],
    )
    async def test_blocks_non_allowlisted_http(self, guarded, method, path) -> None:
        with pytest.raises(AssertionError, match="read-only guard"):
            await getattr(guarded._http, method)(path)

    @pytest.mark.parametrize("name", ["place_order", "modify_order", "cancel_order", "cancel_all"])
    async def test_blocks_order_methods(self, guarded, name) -> None:
        with pytest.raises(AssertionError, match="read-only guard"):
            await getattr(guarded, name)()

    async def test_guard_blocks_before_any_request_is_sent(self, guarded) -> None:
        sent = []
        original = guarded._http._client.request

        async def spy(*a, **kw):
            sent.append(a)
            return await original(*a, **kw)

        guarded._http._client.request = spy
        with pytest.raises(AssertionError):
            await guarded._http.post("/orders", json_body={})
        assert sent == []

    async def test_allows_read_endpoints(self, guarded) -> None:
        assert await guarded.funds() is not None
        await guarded.orders()
        await guarded.positions()
