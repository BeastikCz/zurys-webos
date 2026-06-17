"""Login kalendář: mark aktivního dne (z daily claimu) + milníkové bonusy.

    .venv/Scripts/python.exe -m pytest tests/test_login_calendar.py -v
"""
import json
import secrets


def _mk(conn):
    from app.db import now_iso
    u = f"lc_{secrets.token_hex(3)}"
    uid = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
                       (u, u, "user", now_iso())).lastrowid
    conn.commit()
    return uid


def test_login_calendar_mark_and_milestones(client):
    from app.db import get_conn
    from app import logincal
    conn = get_conn()
    try:
        uid = _mk(conn)
        logincal.mark(conn, uid); conn.commit()
        st = logincal.status(conn, {"id": uid})
        assert st["total"] == 1 and logincal._today_day() in st["active"]

        logincal.mark(conn, uid); conn.commit()        # stejný den → idempotent
        assert logincal.status(conn, {"id": uid})["total"] == 1

        # nasimuluj 5 aktivních dní → milník 5 dosažen
        conn.execute("UPDATE login_calendar SET days=? WHERE user_id=? AND month=?",
                     (json.dumps([1, 2, 3, 4, 5]), uid, logincal._month()))
        conn.commit()
        st = logincal.status(conn, {"id": uid})
        ms5 = next(m for m in st["milestones"] if m["days"] == 5)
        assert ms5["reached"] and not ms5["claimed"]

        r = logincal.claim(conn, {"id": uid}, 5)
        assert r["ok"] and r["reward"] == 200
        assert logincal.claim(conn, {"id": uid}, 5)["ok"] is False     # už vyzvednuto
        assert logincal.claim(conn, {"id": uid}, 10)["ok"] is False    # 10 dní nedosaženo
    finally:
        conn.close()


def test_daily_claim_marks_calendar(client):
    """Vyzvednutí denního streaku označí dnešní den v kalendáři (jeden akt = obojí)."""
    from app.db import get_conn, now_iso
    from app.config import SESSION_COOKIE
    from datetime import datetime, timezone, timedelta
    from app import logincal
    conn = get_conn()
    try:
        uid = _mk(conn)
        t = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (t, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
    finally:
        conn.close()
    r = client.post("/api/daily/claim", headers={"Cookie": f"{SESSION_COOKIE}={t}"})
    assert r.status_code == 200
    conn = get_conn()
    try:
        st = logincal.status(conn, {"id": uid})
        assert logincal._today_day() in st["active"], "denní claim měl označit dnešek v kalendáři"
    finally:
        conn.close()
