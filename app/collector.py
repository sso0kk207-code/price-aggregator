import asyncio
import time
from datetime import UTC, datetime, timedelta
from statistics import median

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AlertEvent, AlertRule, Price, ProviderStatus
from app.providers import Provider

FRESH_FOR = timedelta(minutes=15)  # цены старше не участвуют в консенсусе


async def _call(provider: Provider, symbols: list[str], timeout: float):
    started = time.perf_counter()
    try:
        data = await asyncio.wait_for(provider.fetch(symbols), timeout)
        return provider.name, data, None, int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - падение одного источника не должно ронять сбор остальных
        return provider.name, {}, f"{type(exc).__name__}: {exc}"[:300], int((time.perf_counter() - started) * 1000)


async def collect(db: Session, providers: list[Provider], symbols: list[str], timeout: float = 8.0, http: httpx.AsyncClient | None = None) -> dict:
    """Опрашивает все источники параллельно, сохраняет цены и статусы, затем проверяет алерты."""
    results = await asyncio.gather(*[_call(p, symbols, timeout) for p in providers])
    now = datetime.now(UTC)
    report: dict = {"providers": {}, "prices_saved": 0}
    for name, data, error, latency in results:
        st = db.get(ProviderStatus, name) or ProviderStatus(provider=name, ok_count=0, fail_count=0)
        db.add(st)
        st.last_latency_ms = latency
        if error:
            st.fail_count += 1
            st.last_error = error
            report["providers"][name] = {"ok": False, "error": error}
        else:
            st.ok_count += 1
            st.last_ok_at, st.last_error = now, None
            for sym, price in data.items():
                db.add(Price(symbol=sym, provider=name, price=price, ts=now))
            report["prices_saved"] += len(data)
            report["providers"][name] = {"ok": True, "symbols": len(data), "latency_ms": latency}
    db.commit()
    report["alerts_fired"] = await check_alerts(db, symbols, http)
    return report


def consensus(db: Session, symbol: str, now: datetime | None = None) -> dict | None:
    """Медиана по свежим ценам разных источников (последняя цена каждого). Медиана устойчива к выбросу одного источника."""
    now = now or datetime.now(UTC)
    rows = db.scalars(select(Price).where(Price.symbol == symbol, Price.ts >= now - FRESH_FOR).order_by(Price.ts.desc(), Price.id.desc())).all()
    latest: dict[str, Price] = {}
    for r in rows:
        latest.setdefault(r.provider, r)
    if not latest:
        return None
    prices = [r.price for r in latest.values()]
    mid = median(prices)
    return {
        "symbol": symbol,
        "price": round(mid, 6),
        "sources": [{"provider": p, "price": r.price} for p, r in sorted(latest.items())],
        "spread_pct": round((max(prices) - min(prices)) / mid * 100, 4) if len(prices) > 1 else 0.0,
    }


async def check_alerts(db: Session, symbols: list[str], http: httpx.AsyncClient | None = None) -> int:
    fired = 0
    for rule in db.scalars(select(AlertRule).where(AlertRule.active.is_(True), AlertRule.symbol.in_(symbols))).all():
        c = consensus(db, rule.symbol)
        if not c:
            continue
        price = c["price"]
        hit = price >= rule.threshold if rule.kind == "above" else price <= rule.threshold
        if hit and rule.armed:
            rule.armed = False  # не спамим, пока цена остаётся за порогом
            msg = f"{rule.symbol} = {price} ({'выше' if rule.kind == 'above' else 'ниже'} {rule.threshold})"
            db.add(AlertEvent(rule_id=rule.id, symbol=rule.symbol, price=price, message=msg))
            fired += 1
            if rule.webhook_url:
                await _notify(rule.webhook_url, {"symbol": rule.symbol, "price": price, "message": msg}, http)
        elif not hit and not rule.armed:
            rule.armed = True  # цена вернулась - правило снова взведено
    db.commit()
    return fired


async def _notify(url: str, payload: dict, http: httpx.AsyncClient | None) -> None:
    try:
        client = http or httpx.AsyncClient(timeout=5)
        r = await client.post(url, json=payload)
        r.raise_for_status()
    except Exception:  # noqa: BLE001 - событие уже сохранено, недоставленный вебхук не должен ломать сбор
        pass
