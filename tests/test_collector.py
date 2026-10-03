import asyncio
import time
from datetime import UTC, datetime, timedelta

import httpx

from app.collector import check_alerts, collect, consensus
from app.models import AlertEvent, AlertRule, Price, ProviderStatus
from tests.conftest import FakeProvider

SYM = ["BTC-USD"]


def run(coro):
    return asyncio.run(coro)


def test_providers_are_polled_concurrently(db):
    slow = [FakeProvider(f"p{i}", {"BTC-USD": 100.0}, delay=0.4) for i in range(3)]
    started = time.perf_counter()
    run(collect(db, slow, SYM))
    assert time.perf_counter() - started < 0.9  # последовательно было бы 1.2 с


def test_failing_provider_does_not_break_others(db):
    ok, bad = FakeProvider("ok", {"BTC-USD": 100.0}), FakeProvider("bad", error=ConnectionError("down"))
    report = run(collect(db, [ok, bad], SYM))
    assert report["providers"]["ok"]["ok"] is True
    assert report["providers"]["bad"]["ok"] is False and "ConnectionError" in report["providers"]["bad"]["error"]
    assert report["prices_saved"] == 1
    assert db.get(ProviderStatus, "bad").fail_count == 1
    assert db.get(ProviderStatus, "ok").ok_count == 1


def test_slow_provider_times_out(db):
    report = run(collect(db, [FakeProvider("slow", {"BTC-USD": 1.0}, delay=2)], SYM, timeout=0.1))
    assert report["providers"]["slow"]["ok"] is False
    assert db.query(Price).count() == 0


def test_provider_recovers_and_error_is_cleared(db):
    flaky = FakeProvider("flaky", error=RuntimeError("x"))
    run(collect(db, [flaky], SYM))
    flaky.error, flaky.prices = None, {"BTC-USD": 5.0}
    run(collect(db, [flaky], SYM))
    st = db.get(ProviderStatus, "flaky")
    assert st.last_error is None and (st.ok_count, st.fail_count) == (1, 1)


def test_consensus_is_median_and_resists_outlier(db):
    providers = [FakeProvider("a", {"BTC-USD": 100.0}), FakeProvider("b", {"BTC-USD": 102.0}), FakeProvider("glitch", {"BTC-USD": 9999.0})]
    run(collect(db, providers, SYM))
    c = consensus(db, "BTC-USD")
    assert c["price"] == 102.0
    assert {s["provider"] for s in c["sources"]} == {"a", "b", "glitch"}
    assert c["spread_pct"] > 1000


def test_consensus_uses_latest_price_per_provider(db):
    p = FakeProvider("a", {"BTC-USD": 100.0})
    run(collect(db, [p], SYM))
    p.prices = {"BTC-USD": 110.0}
    run(collect(db, [p], SYM))
    assert consensus(db, "BTC-USD")["price"] == 110.0


def test_stale_prices_are_ignored(db):
    old = datetime.now(UTC) - timedelta(hours=2)
    db.add(Price(symbol="BTC-USD", provider="a", price=1.0, ts=old))
    db.commit()
    assert consensus(db, "BTC-USD") is None


def add_rule(db, kind="above", threshold=105.0, webhook=None):
    rule = AlertRule(symbol="BTC-USD", kind=kind, threshold=threshold, webhook_url=webhook, active=True, armed=True)
    db.add(rule)
    db.commit()
    return rule


def test_alert_fires_once_then_rearms(db):
    rule = add_rule(db, "above", 105.0)
    p = FakeProvider("a", {"BTC-USD": 100.0})
    assert run(collect(db, [p], SYM))["alerts_fired"] == 0
    p.prices = {"BTC-USD": 106.0}
    assert run(collect(db, [p], SYM))["alerts_fired"] == 1
    assert run(collect(db, [p], SYM))["alerts_fired"] == 0  # всё ещё выше порога: не спамим
    p.prices = {"BTC-USD": 101.0}
    run(collect(db, [p], SYM))
    db.refresh(rule)
    assert rule.armed is True
    p.prices = {"BTC-USD": 107.0}
    assert run(collect(db, [p], SYM))["alerts_fired"] == 1  # после возврата сработает снова
    assert db.query(AlertEvent).count() == 2


def test_below_alert(db):
    add_rule(db, "below", 50.0)
    p = FakeProvider("a", {"BTC-USD": 49.0})
    assert run(collect(db, [p], SYM))["alerts_fired"] == 1
    assert "ниже" in db.query(AlertEvent).one().message


def test_webhook_notified_and_failure_is_swallowed(db):
    add_rule(db, "above", 10.0, webhook="https://hooks.example.com/alert")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.content)
        return httpx.Response(500)  # вебхук недоступен

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run(collect(db, [FakeProvider("a", {"BTC-USD": 20.0})], SYM, http=http))
    assert len(calls) == 1
    assert db.query(AlertEvent).count() == 1  # событие сохранено, несмотря на сбой вебхука


def test_inactive_rule_is_ignored(db):
    rule = add_rule(db, "above", 1.0)
    rule.active = False
    db.commit()
    run(collect(db, [FakeProvider("a", {"BTC-USD": 20.0})], SYM))
    assert run(check_alerts(db, SYM)) == 0
