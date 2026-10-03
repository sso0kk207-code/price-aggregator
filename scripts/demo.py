"""Демо-сценарий: python scripts/demo.py http://127.0.0.1:8000   (сервис запущен с PROVIDERS=simulated)"""
from _demo import section, show

section("1. Правило: уведомить, когда BTC-USD поднимется выше 64300")
show("POST", "/alerts/rules", json_body={"symbol": "BTC-USD", "kind": "above", "threshold": 64300}, max_lines=8)

section("2. Три источника опрашиваются параллельно")
for i in range(3):
    r = show("POST", "/collect", max_lines=0 if i < 2 else 30)

section("3. Консенсус-цена: медиана по источникам и разброс")
show("GET", "/prices/BTC-USD", max_lines=25)

section("4. Сработавшие алерты")
show("GET", "/alerts/events", max_lines=12)

section("5. Здоровье источников")
show("GET", "/providers", max_lines=14)
