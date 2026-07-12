from app import config, ddos, deps, main


def test_webos_prod_enables_production_without_fly(monkeypatch):
    monkeypatch.setenv("WEBOS_PROD", "1")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    assert config.is_production() is True


def test_fly_remains_production_fallback(monkeypatch):
    monkeypatch.delenv("WEBOS_PROD", raising=False)
    monkeypatch.setenv("FLY_APP_NAME", "zurys-shop")
    assert config.is_production() is True


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
