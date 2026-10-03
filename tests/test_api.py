from app.main import app
from tests.conftest import FakeProvider


def setup_providers(prices=(100.0, 102.0, 101.0)):
    app.state.providers = [FakeProvider(f"p{i}", {"BTC-USD": v, "ETH-USD": v / 20}) for i, v in enumerate(prices)]


def test_collect_then_prices(client):
    setup_providers()
    r = client.post("/collect").json()
    assert r["prices_saved"] == 6 and all(p["ok"] for p in r["providers"].values())
    prices = {p["symbol"]: p for p in client.get("/prices").json()}
    assert prices["BTC-USD"]["price"] == 101.0 and len(prices["BTC-USD"]["sources"]) == 3
    assert "EUR-USD" not in prices  # по нему данных нет


def test_single_symbol_and_404(client):
    setup_providers()
    client.post("/collect")
    assert client.get("/prices/btc-usd").json()["price"] == 101.0  # регистр не важен
    assert client.get("/prices/EUR-USD").status_code == 404


def test_history_grows_with_each_collect(client):
    setup_providers()
    for _ in range(3):
        client.post("/collect")
    h = client.get("/prices/BTC-USD/history").json()
    assert len(h) == 9 and h[0]["ts"] <= h[-1]["ts"]
    assert len(client.get("/prices/BTC-USD/history?limit=4").json()) == 4


def test_provider_status_endpoint(client):
    app.state.providers = [FakeProvider("good", {"BTC-USD": 1.0}), FakeProvider("broken", error=TimeoutError("t"))]
    client.post("/collect")
    st = {p["provider"]: p for p in client.get("/providers").json()}
    assert st["good"]["healthy"] is True and st["broken"]["healthy"] is False
    assert "TimeoutError" in st["broken"]["last_error"]


def test_alert_rule_flow(client):
    setup_providers((100.0,))
    rule = client.post("/alerts/rules", json={"symbol": "btc-usd", "kind": "above", "threshold": 99}).json()
    assert rule["symbol"] == "BTC-USD" and rule["armed"] is True
    assert client.post("/collect").json()["alerts_fired"] == 1
    events = client.get("/alerts/events").json()
    assert len(events) == 1 and events[0]["rule_id"] == rule["id"]
    assert client.get("/alerts/rules").json()[0]["armed"] is False
    assert client.delete(f"/alerts/rules/{rule['id']}").status_code == 204
    assert client.delete(f"/alerts/rules/{rule['id']}").status_code == 404


def test_rule_validation(client):
    assert client.post("/alerts/rules", json={"symbol": "DOGE-USD", "kind": "above", "threshold": 1}).status_code == 422
    assert client.post("/alerts/rules", json={"symbol": "BTC-USD", "kind": "sideways", "threshold": 1}).status_code == 422
    assert client.post("/alerts/rules", json={"symbol": "BTC-USD", "kind": "above", "threshold": -1}).status_code == 422
    assert client.post("/alerts/rules", json={"symbol": "BTC-USD", "kind": "above", "threshold": 1, "webhook_url": "ftp://x"}).status_code == 422
