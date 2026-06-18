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


def test_static_assets_bypass_maintenance(client, maint_on):
    """JS/CSS musí projít i během údržby. Jinak by /app.js vracelo údržbové HTML,
    Cloudflare si ho zacachuje pod URL assetu (.js) a po vypnutí údržby servíruje
    HTML místo JS → rozbitý web. (Návštěvník dál vidí údržbu na '/'.)"""
    for path in ("/app.js", "/styles.css"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} mělo projít (200), je {r.status_code}"
        assert "X-Maintenance" not in r.headers, f"{path} nesmí být údržbová HTML stránka"
        assert "text/html" not in r.headers.get("content-type", ""), f"{path} nesmí být HTML"


def test_root_still_shows_maintenance(client, maint_on):
    """Pojistka: SPA shell na '/' návštěvníkovi dál ukazuje údržbu (X-Maintenance)."""
    assert client.get("/").headers.get("X-Maintenance") == "1"


def test_allowlisted_user_bypasses_maintenance(client, maint_on):
    """Uživatel v maintenance_allow_uids vidí web (běžné API projde) i během údržby; jiný ne."""
    import secrets, json
    from app.db import get_conn, now_iso, set_setting
    from app.config import SESSION_COOKIE
    conn = get_conn()
    try:
        uname = f"mn_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (uname, uname, "user", now_iso())).lastrowid
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        set_setting(conn, "maintenance_allow_uids", json.dumps([uid]))
        conn.commit()
    finally:
        conn.close()
    h = {"Cookie": f"{SESSION_COOKIE}={tok}"}
    try:
        assert client.get("/api/shop/products", headers=h).status_code == 200, "allowlistnutý má vidět web"
        # bez allowlistu → zase 503
        c2 = get_conn()
        try:
            set_setting(c2, "maintenance_allow_uids", "[]")
            c2.commit()
        finally:
            c2.close()
        assert client.get("/api/shop/products", headers=h).status_code == 503, "mimo allowlist → údržba"
    finally:
        c3 = get_conn()           # úklid pro ostatní testy
        try:
            set_setting(c3, "maintenance_allow_uids", "")
            c3.commit()
        finally:
            c3.close()
