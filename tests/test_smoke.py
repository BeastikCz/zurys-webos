"""Smoke testy: chytí rozbití (špatný import, syntaxe, mrtvý endpoint, 500 místo
401) PŘED deployem. Nejsou to úplné funkční testy – jde o rychlou pojistku.

    .venv/Scripts/python.exe -m pytest tests/ -q
"""
from datetime import datetime, timezone, timedelta

import pytest


def test_app_imports():
    # když jsme tady, app.main se naimportoval bez chyby (přes conftest) = nejdůležitější pojistka
    from app.main import app
    assert app is not None


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_monitor_healthz(client):
    r = client.get("/api/monitor/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"
    assert data.get("checks", {}).get("db") == "ok"


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "ZURYS" in r.text


@pytest.mark.parametrize("path", [
    "/api/shop/products",
    "/api/news",
    "/api/leaderboard",
    "/api/drops/active",
])
def test_public_endpoints_ok(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"


@pytest.mark.parametrize("path", [
    "/api/wheel/status",
    "/api/daily/status",
    "/api/admin/products",
    "/api/admin/overview",
    "/api/admin/checklist",
    "/api/admin/economy/dashboard",
    "/api/admin/maintenance",
])
def test_auth_required(client, path):
    # nepřihlášený musí dostat 401 (route žije, jen chce login) – NE 500 ani 404
    r = client.get(path)
    assert r.status_code == 401, f"{path} -> {r.status_code}"


def test_maintenance_toggle_needs_admin(client):
    r = client.post("/api/admin/maintenance?to=on")
    assert r.status_code == 401


def test_admin_mutation_rejects_missing_origin_in_prod(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_PROD", True)
    r = client.post("/api/admin/maintenance?to=on")
    assert r.status_code == 403


def test_admin_mutation_rejects_cross_origin_in_prod(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_PROD", True)
    r = client.post(
        "/api/admin/maintenance?to=on",
        headers={"origin": "https://evil.example", "host": "zurys.live"},
    )
    assert r.status_code == 403


def test_admin_mutation_allows_same_origin_then_auth_in_prod(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_PROD", True)
    r = client.post(
        "/api/admin/maintenance?to=on",
        headers={"origin": "https://zurys.live", "host": "zurys.live"},
    )
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/og-image.png", "/maintenance.html", "/maintenance.png"])
def test_static_assets(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_maintenance_countdown_autoexpires():
    """Odpočet v minulosti → is_on() se sám vypne (auto-switch zpět na web)."""
    from app import maintenance
    maintenance._on = True
    maintenance._until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert maintenance.is_on() is False          # prošlý odpočet → auto-off
    maintenance._on = True
    maintenance._until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert maintenance.is_on() is True            # budoucí odpočet → běží
    maintenance._on = False
    maintenance._until = ""                        # úklid stavu po testu


def test_duels_auth_required(client):
    assert client.get("/api/games/duels/open").status_code == 401
    assert client.post("/api/games/duels/create", json={"type": "coinflip", "stake": 100}).status_code == 401
