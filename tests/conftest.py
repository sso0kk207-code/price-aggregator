import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite://"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app


class FakeProvider:
    def __init__(self, name, prices=None, error=None, delay=0.0):
        self.name, self.prices, self.error, self.delay = name, prices or {}, error, delay

    async def fetch(self, symbols):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return {s: p for s, p in self.prices.items() if s in symbols}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
