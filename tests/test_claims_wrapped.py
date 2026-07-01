"""/me/claims agregát (tečky+panel+streak) a měsíční Wrapped push tick.

    .venv/Scripts/python.exe -m pytest tests/test_claims_wrapped.py -v
"""
import secrets


def _mk_session(conn, points=500):
    from app.db import now_iso
    from datetime import datetime, timezone, timedelta
    u = f"c_{secrets.token_hex(3)}"
    uid = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
                       (u, u, "user", points, now_iso())).lastrowid
    tok = f"tok_{secrets.token_hex(8)}"
    exp = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (tok, uid, now_iso(), exp))
    conn.commit()
    return uid, tok


def test_me_claims_aggregate(client):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        uid, tok = _mk_session(conn)
        # čerstvý user: daily i kolo jdou vyzvednout, nic dalšího
        r = client.get("/api/me/claims", cookies={"webos_session": tok})
        assert r.status_code == 200
        d = r.json()
        assert d["daily"] is True and d["wheel"] is True
        assert d["garden"] == 0 and d["streak"] == 0
        for k in ("quests", "battlepass", "levelpass", "partner"):
            assert isinstance(d[k], int)

        # dozrálá úroda + streak → agregát je vidí
        conn.execute("INSERT INTO garden (user_id, plot, crop, planted_at, ready_at, pest) "
                     "VALUES (?,0,'mrkev',?,?,0)", (uid, now_iso(), "2000-01-01T00:00:00+00:00"))
        conn.execute("UPDATE users SET daily_streak=4, last_daily=? WHERE id=?", (now_iso(), uid))
        conn.commit()
        d2 = client.get("/api/me/claims", cookies={"webos_session": tok}).json()
        assert d2["garden"] == 1
        assert d2["streak"] == 4 and d2["daily"] is False   # právě vyzvednuto → cooldown
    finally:
        conn.close()


def test_me_claims_requires_login(client):
    assert client.get("/api/me/claims").status_code == 401


def test_wrapped_push_tick_once_per_month(client, monkeypatch):
    """Flag zaručí 1× za měsíc; mimo 1. den se nic neděje."""
    import datetime as dt
    from app.db import get_conn, get_setting
    from app import wrapped_push
    conn = get_conn()
    try:
        # dnes může být reálně 1. den měsíce → ostrý daemon (start při importu appky) flag už
        # mohl nastavit; test si začíná s čistým stolem
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'wrapped_push_%'")
        conn.commit()
        sent = []
        monkeypatch.setattr(wrapped_push.webpush, "send",
                            lambda info, title, body="", url="/", icon="": sent.append(title) or True)

        class FakeNow:
            def __init__(self, y, m, d): self.year, self.month, self.day = y, m, d
        # 2. den v měsíci → nic
        monkeypatch.setattr(wrapped_push, "local_now", lambda: FakeNow(2026, 7, 2))
        wrapped_push._tick(conn)
        assert get_setting(conn, "wrapped_push_2026-07", "") == ""

        # 1. den → flag + odeslání na existující suby (klidně 0 subů — flag se nastaví tak jako tak)
        monkeypatch.setattr(wrapped_push, "local_now", lambda: FakeNow(2026, 7, 1))
        conn.execute("INSERT OR IGNORE INTO push_subs (user_id, endpoint, p256dh, auth, created_at) "
                     "SELECT id, 'https://push.test/ep-'||id, 'k', 'a', created_at FROM users LIMIT 2")
        conn.commit()
        wrapped_push._tick(conn)
        assert get_setting(conn, "wrapped_push_2026-07", "") == "1"
        assert all("červen" in t for t in sent) and len(sent) >= 1   # předchozí měsíc = červen

        # druhý tick téhož měsíce → žádné další odeslání
        n0 = len(sent)
        wrapped_push._tick(conn)
        assert len(sent) == n0
    finally:
        conn.close()
