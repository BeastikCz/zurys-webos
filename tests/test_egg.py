"""Easter egg „tajný klas": 1×/DEN, deterministická denní odměna (500–2500), body ANO / XP NE.

    .venv/Scripts/python.exe -m pytest tests/test_egg.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _user(conn):
    from app.db import now_iso
    uname = f"egg_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, earned_total, created_at) "
        "VALUES (?,?,?,0,0,?)", (uname, uname, "user", now_iso())).lastrowid
    tok = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (tok, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit()
    return uid, tok


def test_egg_reward_today_deterministic():
    from app.routers.misc import egg_reward_today
    a, b = egg_reward_today(), egg_reward_today()
    assert a == b                                   # stejné v rámci dne (nejde rerollnout)
    assert 500 <= a <= 2500 and a % 100 == 0        # rozsah + krok 100


def test_egg_daily_claim_once_and_no_xp(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        uid, tok = _user(conn)
    finally:
        conn.close()
    h = {"Cookie": f"{SESSION_COOKIE}={tok}"}
    r1 = client.post("/api/egg/claim", headers=h).json()
    assert r1["ok"] is True and r1["reward"] >= 500
    r2 = client.post("/api/egg/claim", headers=h).json()    # podruhé TÝŽ den → already
    assert r2.get("already") is True
    conn = get_conn()
    try:
        row = conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
        assert row["points"] == r1["reward"], "body připsané"
        assert row["earned_total"] == 0, "egg je luck bonus → 0 XP (xp=False)"
    finally:
        conn.close()
