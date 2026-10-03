import asyncio

import httpx

from app.providers import CoinGeckoProvider, FrankfurterProvider, SimulatedProvider


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_coingecko_parses_and_maps_symbols():
    seen = {}

    def handler(request: httpx.Request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"bitcoin": {"usd": 64123.5}, "ethereum": {"usd": 3111.2}})

    p = CoinGeckoProvider(mock_client(handler))
    got = asyncio.run(p.fetch(["BTC-USD", "ETH-USD", "EUR-USD"]))
    assert got == {"BTC-USD": 64123.5, "ETH-USD": 3111.2}
    assert seen["params"]["ids"] == "bitcoin,ethereum"  # EUR-USD источник не знает и не запрашивает


def test_coingecko_skips_request_when_nothing_to_ask():
    def handler(request):
        raise AssertionError("no request expected")

    assert asyncio.run(CoinGeckoProvider(mock_client(handler)).fetch(["EUR-USD"])) == {}


def test_frankfurter_parses_rate():
    def handler(request: httpx.Request):
        assert request.url.params["from"] == "EUR"
        return httpx.Response(200, json={"rates": {"USD": 1.0842}})

    assert asyncio.run(FrankfurterProvider(mock_client(handler)).fetch(["EUR-USD"])) == {"EUR-USD": 1.0842}


def test_http_error_propagates_for_collector_to_handle():
    p = FrankfurterProvider(mock_client(lambda r: httpx.Response(500)))
    try:
        asyncio.run(p.fetch(["EUR-USD"]))
    except httpx.HTTPStatusError:
        return
    raise AssertionError("expected HTTPStatusError")


def test_simulated_provider_is_deterministic_and_drifts():
    a, b = SimulatedProvider("x"), SimulatedProvider("x")
    first_a, first_b = asyncio.run(a.fetch(["BTC-USD"])), asyncio.run(b.fetch(["BTC-USD"]))
    assert first_a == first_b
    assert asyncio.run(a.fetch(["BTC-USD"]))["BTC-USD"] > first_a["BTC-USD"]
