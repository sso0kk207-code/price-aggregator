import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector import collect, consensus
from app.db import Base, SessionLocal, engine, get_db
from app.models import AlertEvent, AlertRule, Price, ProviderStatus
from app.providers import providers_from_env

SYMBOLS = os.getenv("SYMBOLS", "BTC-USD,ETH-USD,EUR-USD").split(",")


async def poll_loop(app: FastAPI, every: float) -> None:
    while True:
        with SessionLocal() as db:
            await collect(db, app.state.providers, SYMBOLS)
        await asyncio.sleep(every)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    app.state.providers = providers_from_env(os.getenv("PROVIDERS", "simulated"))
    task = None
    if (every := float(os.getenv("POLL_SECONDS", "0"))) > 0:
        task = asyncio.create_task(poll_loop(app, every))
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="Price Aggregator API",
    version="1.0.0",
    description="Сбор цен из нескольких источников параллельно, консенсус-цена (медиана), история, статус источников и алерты по порогам.",
    lifespan=lifespan,
)


class RuleIn(BaseModel):
    symbol: str
    kind: Literal["above", "below"]
    threshold: float = Field(gt=0)
    webhook_url: str | None = Field(default=None, pattern=r"^https?://")


class RuleOut(RuleIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    active: bool
    armed: bool


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_id: int
    symbol: str
    price: float
    message: str
    ts: datetime


@app.get("/health", tags=["service"])
def health():
    return {"status": "ok"}


@app.post("/collect", tags=["collector"], summary="Опросить все источники прямо сейчас")
async def collect_now(db: Session = Depends(get_db)):
    return await collect(db, app.state.providers, SYMBOLS)


@app.get("/prices", tags=["prices"], summary="Консенсус-цены по всем символам")
def latest_prices(db: Session = Depends(get_db)):
    return [c for s in SYMBOLS if (c := consensus(db, s))]


@app.get("/prices/{symbol}", tags=["prices"])
def price(symbol: str, db: Session = Depends(get_db)):
    c = consensus(db, symbol.upper())
    if not c:
        raise HTTPException(404, "No fresh prices for this symbol")
    return c


@app.get("/prices/{symbol}/history", tags=["prices"])
def history(symbol: str, hours: int = Query(24, ge=1, le=168), limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = db.scalars(select(Price).where(Price.symbol == symbol.upper(), Price.ts >= since).order_by(Price.ts.desc(), Price.id.desc()).limit(limit)).all()
    return [{"ts": r.ts, "provider": r.provider, "price": r.price} for r in reversed(rows)]


@app.get("/providers", tags=["collector"], summary="Состояние источников: последний успех, ошибка, задержка")
def providers(db: Session = Depends(get_db)):
    return [
        {"provider": s.provider, "healthy": s.last_error is None, "last_ok_at": s.last_ok_at, "last_error": s.last_error, "last_latency_ms": s.last_latency_ms, "ok": s.ok_count, "failed": s.fail_count}
        for s in db.scalars(select(ProviderStatus).order_by(ProviderStatus.provider))
    ]


@app.post("/alerts/rules", response_model=RuleOut, status_code=201, tags=["alerts"])
def create_rule(data: RuleIn, db: Session = Depends(get_db)):
    if data.symbol.upper() not in SYMBOLS:
        raise HTTPException(422, f"Unknown symbol. Available: {', '.join(SYMBOLS)}")
    rule = AlertRule(**{**data.model_dump(), "symbol": data.symbol.upper()}, active=True, armed=True)
    db.add(rule)
    db.commit()
    return rule


@app.get("/alerts/rules", response_model=list[RuleOut], tags=["alerts"])
def list_rules(db: Session = Depends(get_db)):
    return db.scalars(select(AlertRule).order_by(AlertRule.id)).all()


@app.delete("/alerts/rules/{rule_id}", status_code=204, tags=["alerts"])
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.delete(rule)
    db.commit()


@app.get("/alerts/events", response_model=list[EventOut], tags=["alerts"])
def events(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return db.scalars(select(AlertEvent).order_by(AlertEvent.id.desc()).limit(limit)).all()
