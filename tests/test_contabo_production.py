import os
import subprocess
import sys
from datetime import datetime

from fastapi import HTTPException

from app import config, ddos, deps, ipban, main
from app.db import get_conn


def test_webos_prod_enables_production_without_fly(monkeypatch):
    monkeypatch.setenv("WEBOS_PROD", "1")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    assert config.is_production() is True


def test_fly_remains_production_fallback(monkeypatch):
    monkeypatch.delenv("WEBOS_PROD", raising=False)
    monkeypatch.setenv("FLY_APP_NAME", "zurys-shop")
    assert config.is_production() is True


def test_webos_prod_boots_main_and_oauth_in_production(tmp_path):
    env = os.environ | {
        "WEBOS_DATA_DIR": str(tmp_path),
        "WEBOS_PROD": "1",
        "KICK_CLIENT_ID": "test-id",
        "KICK_CLIENT_SECRET": "test-secret",
    }
    code = (
        "from app import main; from app.routers import auth; "
        "assert main._PROD and main.app.docs_url is None and main.app.openapi_url is None; "
        "assert auth.IS_PRODUCTION and auth.OAUTH_ENABLED; "
        "assert 'Strict-Transport-Security' in main._SECURITY_HEADERS"
    )
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_without_proxy_headers_skips_ddos(client, monkeypatch):
    seen = []
    monkeypatch.setattr(ddos, "observe", lambda ip: seen.append(ip) or 0)

    assert client.get("/api/health").status_code == 200
    assert seen == []


def test_cloudflare_header_enters_ddos_guard(client, monkeypatch):
    seen = []
    monkeypatch.setattr(deps, "_ORIGIN_LOCK_ACTIVE", True)
    monkeypatch.setattr(ddos, "observe", lambda ip: seen.append(ip) or 0)

    # DDoS guard is skipped on origin-lock-free routes (/api/health, /api/monitor/healthz, /api/_origin_check)
    # Test /api/news (public route) to verify CF header enters DDoS observation
    assert client.get("/api/news", headers={"cf-connecting-ip": "203.0.113.10"}).status_code == 200
    assert seen == ["203.0.113.10"]


def test_api_path_flood_is_throttled_before_app_and_autoban(client, monkeypatch):
    seen, limits = [], []
    def blocked(*args):
        limits.append(args)
        raise HTTPException(429)

    monkeypatch.setattr(deps, "_ORIGIN_LOCK_ACTIVE", True)
    monkeypatch.setattr(main, "rate_limit", blocked)
    monkeypatch.setattr(ddos, "observe", lambda ip: seen.append(ip) or 0)

    response = client.get("/api/news", headers={"cf-connecting-ip": "203.0.113.10"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert limits == [("api-path:203.0.113.10:/api/news", 300, 60)]
    assert seen == []


def test_autoban_is_one_hour_and_survives_reload():
    ip = "203.0.113.77"
    conn = get_conn()
    try:
        ipban.unban(conn, ip)
        conn.commit()
        assert ddos.AUTOBAN_PER_MIN == 1000
        assert ddos.AUTOBAN_MINUTES == 60
        assert ipban.temp_ban(ip, "test auto-ban", ddos.AUTOBAN_MINUTES)

        row = conn.execute("SELECT created_at, expires_at FROM ip_bans WHERE ip = ?", (ip,)).fetchone()
        assert row is not None
        assert 3590 <= (datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])).total_seconds() <= 3610
        ipban.load(conn)
        assert ipban.check(ip) is not None
    finally:
        ipban.unban(conn, ip)
        conn.commit()
        conn.close()
