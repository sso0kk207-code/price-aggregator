"""Источники цен. Контракт: `await provider.fetch(symbols) -> {symbol: price}` (символы, которых у источника нет, пропускаются)."""
import hashlib
from typing import Protocol

import httpx


class Provider(Protocol):
    name: str

    async def fetch(self, symbols: list[str]) -> dict[str, float]: ...


class CoinGeckoProvider:
    """Крипта: https://api.coingecko.com/api/v3/simple/price"""

    name = "coingecko"
    IDS = {"BTC-USD": "bitcoin", "ETH-USD": "ethereum"}

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=8)

    async def fetch(self, symbols: list[str]) -> dict[str, float]:
        wanted = {self.IDS[s]: s for s in symbols if s in self.IDS}
        if not wanted:
            return {}
        r = await self.client.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": ",".join(wanted), "vs_currencies": "usd"})
        r.raise_for_status()
        data = r.json()
        return {sym: float(data[cid]["usd"]) for cid, sym in wanted.items() if cid in data}


class FrankfurterProvider:
    """Курсы валют ЕЦБ: https://api.frankfurter.app"""

    name = "frankfurter"
    PAIRS = {"EUR-USD": ("EUR", "USD"), "GBP-USD": ("GBP", "USD")}

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=8)

    async def fetch(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in symbols:
            if s not in self.PAIRS:
                continue
            base, quote = self.PAIRS[s]
            r = await self.client.get("https://api.frankfurter.app/latest", params={"from": base, "to": quote})
            r.raise_for_status()
            out[s] = float(r.json()["rates"][quote])
        return out


class SimulatedProvider:
    """Детерминированный «рынок» для демо и тестов: цена = база * (1 + шум источника) * (1 + дрейф по тикам)."""

    BASE = {"BTC-USD": 64000.0, "ETH-USD": 3100.0, "EUR-USD": 1.08, "GBP-USD": 1.27}

    def __init__(self, name: str, bias: float = 0.0, drift_per_tick: float = 0.002):
        self.name, self.bias, self.drift, self.tick = name, bias, drift_per_tick, 0

    async def fetch(self, symbols: list[str]) -> dict[str, float]:
        self.tick += 1
        out = {}
        for s in symbols:
            if s not in self.BASE:
                continue
            h = int(hashlib.md5(f"{self.name}{s}{self.tick}".encode()).hexdigest()[:6], 16) / 0xFFFFFF  # 0..1
            noise = (h - 0.5) * 0.002
            out[s] = round(self.BASE[s] * (1 + self.bias + noise) * (1 + self.drift * self.tick), 4)
        return out


def providers_from_env(mode: str) -> list[Provider]:
    if mode == "live":
        return [CoinGeckoProvider(), FrankfurterProvider()]
    return [SimulatedProvider("sim-alpha", bias=0.0005), SimulatedProvider("sim-beta", bias=-0.0004), SimulatedProvider("sim-gamma", bias=0.0001)]
