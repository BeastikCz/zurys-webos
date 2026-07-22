from fastapi import HTTPException

from app import ddos, deps, main


def test_passive_poll_flood_is_throttled_before_app(client, monkeypatch):
    seen = []

    def blocked(key, limit, window):
        seen.append((key, limit, window))
        if key.startswith("api-poll:"):
            raise HTTPException(429)

    monkeypatch.setattr(deps, "_ORIGIN_LOCK_ACTIVE", True)
    monkeypatch.setattr(main, "rate_limit", blocked)
    monkeypatch.setattr(ddos, "observe", lambda _ip: (_ for _ in ()).throw(AssertionError("too late")))

    response = client.get("/api/auctions", headers={"cf-connecting-ip": "203.0.113.10"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert seen == [("api-poll:203.0.113.10", 120, 60)]


def test_frontend_deduplicates_gets_and_slows_background_polling():
    js = (main.WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "const _apiGetPending = new Map();" in js
    assert "if (existing) return existing;" in js
    assert "60000 + Math.random() * 30000" in js
    assert "90000 + Math.random() * 30000" in js
    assert "Date.now() - _streamStatusAt >= 120000" in js
    assert "setInterval(refreshStreamDot, 120000)" in js
    assert "window._nxTimer = setInterval(_nxPoll, 60000)" in js
