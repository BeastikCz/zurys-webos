"""Údržba: health + monitoring endpointy musí projít i během údržby (kvůli
externímu uptime monitoru / Fly), kdežto běžné API se zavře (503).

    .venv/Scripts/python.exe -m pytest tests/test_maintenance_allowlist.py -v
"""
from datetime import datetime, timezone, timedelta

import pytest

from app import maintenance


@pytest.fixture
def maint_on():
    """Zapne údržbu na dobu testu a po něm uklidí (ať neovlivní jiné testy)."""
    prev_on, prev_until = maintenance._on, maintenance._until
    maintenance._on = True
    maintenance._until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert maintenance.is_on() is True
    try:
        yield
    finally:
        maintenance._on, maintenance._until = prev_on, prev_until


def test_health_endpoints_bypass_maintenance(client, maint_on):
    """Oba health endpointy musí během údržby vracet 200 (ne 503)."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/monitor/healthz").status_code == 200      # nově ve výjimkách


def test_regular_api_blocked_during_maintenance(client, maint_on):
    """Kontrola, že údržba opravdu drží: běžné API je 503."""
    r = client.get("/api/shop/products")
    assert r.status_code == 503, f"běžné API mělo být 503, je {r.status_code}"
