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


def test_me_claims_onboarding_fields(client):
    """is_new: čerstvý účet ano, starý ne; onb_planted po zasazení (dle points_log)."""
    from app.db import get_conn, now_iso
    from datetime import datetime, timezone, timedelta
    conn = get_conn()
    try:
        uid, tok = _mk_session(conn)
        d = client.get("/api/me/claims", cookies={"webos_session": tok}).json()
        assert d["is_new"] is True and d["onb_planted"] is False
        assert d["onb_animal"] is False and d["onb_crew"] is False
        assert d["farm_ready"] == 0 and d["farm_hungry"] == 0

        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, -20, "Zasazení: mrkev (záhon 1)", now_iso()))
        conn.execute("INSERT INTO farm_collection (user_id,animal_key,created_at) VALUES (?,?,?)",
                     (uid, "chicken", now_iso()))
        conn.execute("INSERT INTO farm_animals (user_id,slot,animal_key,ready_at,bought_at) VALUES (?,?,?,?,?)",
                     (uid, 0, "chicken", "", now_iso()))
        crew_id = conn.execute(
            "INSERT INTO crews (name,tag,emblem,leader_id,code,created_at) VALUES (?,?,?,?,?,?)",
            (f"crew_{uid}", f"C{uid}", "🌾", uid, f"code_{uid}", now_iso())).lastrowid
        conn.execute("INSERT INTO crew_members (crew_id,user_id,role,joined_at) VALUES (?,?,'leader',?)",
                     (crew_id, uid, now_iso()))
        conn.commit()
        d2 = client.get("/api/me/claims", cookies={"webos_session": tok}).json()
        assert d2["onb_planted"] is True and d2["onb_animal"] is True and d2["onb_crew"] is True
        assert d2["farm_hungry"] == 1

        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute("UPDATE users SET created_at=? WHERE id=?", (old, uid))
        conn.commit()
        assert client.get("/api/me/claims", cookies={"webos_session": tok}).json()["is_new"] is False
    finally:
        conn.close()


def test_weekly_summary(client):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        uid, tok = _mk_session(conn)
        sender, _ = _mk_session(conn)
        conn.executemany(
            "INSERT INTO points_log (user_id,change,reason,created_at) VALUES (?,?,?,?)",
            [(uid, 500, "Sklizeň: mrkev 🌾", now_iso()),
             (uid, 300, "Statek: vejce 🥚", now_iso()),
             (uid, 999, "Dar od kamaráda 🎁", now_iso())])
        conn.execute("INSERT INTO orders (user_id,product_name,points_spent,status,created_at) VALUES (?,?,?,'pending',?)",
                     (uid, "Test", 100, now_iso()))
        conn.execute("INSERT INTO gift_requests (from_user_id,to_user_id,amount,status,created_at,decided_at) "
                     "VALUES (?,?,250,'approved',?,?)", (sender, uid, now_iso(), now_iso()))
        conn.commit()
        d = client.get("/api/me/weekly-summary", cookies={"webos_session": tok}).json()
        assert d["earned"] == 800 and d["harvests"] == 1 and d["farm_products"] == 1
        assert d["orders"] == 1 and d["gifts_received"] == 250
    finally:
        conn.close()


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
