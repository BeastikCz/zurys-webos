"""Obnova denního streaku za sedláky (sink, 1×/měsíc, okno do dalšího claimu).

    .venv/Scripts/python.exe -m pytest tests/test_streak_restore.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta


def _mk_logged(conn, points=100000, streak=10, missed_h=50):
    """User se streakem a last_daily před missed_h hodinami (50 h > 48 h reset okno) + session."""
    from app.db import now_iso
    u = f"sr_{secrets.token_hex(3)}"
    last = (datetime.now(timezone.utc) - timedelta(hours=missed_h)).isoformat()
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at, last_daily, daily_streak) "
        "VALUES (?,?, 'user', ?, ?, ?, ?)", (u, u, points, now_iso(), last, streak)).lastrowid
    t = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (t, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit()
    return uid, t


def _cookie(t):
    from app.config import SESSION_COOKIE
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def test_restore_full_flow(client):
    from app.db import get_conn
    from app.routers.misc import _restore_cost
    conn = get_conn()
    try:
        uid, t = _mk_logged(conn, streak=10)
    finally:
        conn.close()

    r = client.post("/api/daily/claim", headers=_cookie(t)).json()   # reset → streak 1, nabídka 10
    assert r["streak"] == 1 and r["streak_lost"] == 10 and r["restore_available"]
    cost = r["restore_cost"]
    assert cost == _restore_cost(10)

    st = client.get("/api/daily/status", headers=_cookie(t)).json()  # nabídka přežije reload
    assert st["streak_lost"] == 10 and st["restore_available"]

    conn = get_conn()
    try:
        before = conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()
    r2 = client.post("/api/daily/restore", headers=_cookie(t)).json()
    assert r2["ok"] and r2["streak"] == 11                           # 10 ztracených + dnešní claim
    conn = get_conn()
    try:
        u = conn.execute("SELECT points, daily_streak, streak_lost FROM users WHERE id=?", (uid,)).fetchone()
        assert u["points"] == before - cost and u["daily_streak"] == 11 and u["streak_lost"] == 0
    finally:
        conn.close()

    r3 = client.post("/api/daily/restore", headers=_cookie(t))       # podruhé není co obnovovat
    assert r3.status_code == 400


def test_restore_month_gate_and_min_streak(client):
    from app.db import get_conn, local_date
    conn = get_conn()
    try:
        uid, t = _mk_logged(conn, streak=10)
        conn.execute("UPDATE users SET streak_restore_month=? WHERE id=?", (local_date()[:7], uid))
        conn.commit()
    finally:
        conn.close()
    client.post("/api/daily/claim", headers=_cookie(t))
    r = client.post("/api/daily/restore", headers=_cookie(t))        # měsíční gate
    assert r.status_code == 400 and "měsíc" in r.json()["detail"]

    conn = get_conn()
    try:
        _, t2 = _mk_logged(conn, streak=2)                           # streak 2 < min 3 → žádná nabídka
    finally:
        conn.close()
    r2 = client.post("/api/daily/claim", headers=_cookie(t2)).json()
    assert r2["streak_lost"] == 0


def test_restore_needs_funds_and_next_claim_clears(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        uid, t = _mk_logged(conn, points=10, streak=10)              # chudák — na obnovu nemá
    finally:
        conn.close()
    client.post("/api/daily/claim", headers=_cookie(t))
    r = client.post("/api/daily/restore", headers=_cookie(t))
    assert r.status_code == 400 and "sedlák" in r.json()["detail"]

    conn = get_conn()                                                # další claim (nový den) nabídku maže
    try:
        prev = (datetime.now(timezone.utc) - timedelta(hours=21)).isoformat()
        conn.execute("UPDATE users SET last_daily=? WHERE id=?", (prev, uid))
        conn.commit()
    finally:
        conn.close()
    r2 = client.post("/api/daily/claim", headers=_cookie(t)).json()
    assert r2["streak"] == 2 and r2["streak_lost"] == 0
    assert client.post("/api/daily/restore", headers=_cookie(t)).status_code == 400
