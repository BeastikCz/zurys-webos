import os
import subprocess
import sys

from app import config, ddos, deps, main


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

    assert client.get("/api/health", headers={"cf-connecting-ip": "203.0.113.10"}).status_code == 200
    assert seen == ["203.0.113.10"]
