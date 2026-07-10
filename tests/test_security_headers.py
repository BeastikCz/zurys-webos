from app import main


def _enable_production_headers(monkeypatch):
    monkeypatch.setattr(main, "_PROD", True)
    monkeypatch.setitem(
        main._SECURITY_HEADERS,
        "Strict-Transport-Security",
        "max-age=31536000; includeSubDomains",
    )


def _assert_security_headers(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "geolocation=(), microphone=(), camera=()"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_security_headers_wrap_normal_and_maintenance_responses(client, monkeypatch):
    _enable_production_headers(monkeypatch)

    normal = client.get("/")
    assert normal.status_code == 200
    assert normal.headers["content-security-policy"] == main._CSP_STRICT
    _assert_security_headers(normal)

    monkeypatch.setattr(main.maintenance, "_on", True)
    maintenance = client.get("/")
    assert maintenance.status_code == 503
    assert maintenance.headers["retry-after"] == "300"
    assert maintenance.headers["content-security-policy"] == main._CSP_RELAXED
    _assert_security_headers(maintenance)


def test_security_txt_remains_available_during_maintenance(client, monkeypatch):
    _enable_production_headers(monkeypatch)
    monkeypatch.setattr(main.maintenance, "_on", True)

    response = client.get("/.well-known/security.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Contact: https://www.instagram.com/interaty_/" in response.text
    _assert_security_headers(response)
