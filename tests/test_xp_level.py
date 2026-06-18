"""XP / Level: level odvozený z earned_total (lifetime nafarmeno, nikdy neklesá).

    .venv/Scripts/python.exe -m pytest tests/test_xp_level.py -v
"""
import secrets


def test_level_info_formula():
    from app.deps import level_info, XP_DIV
    assert level_info(0)["level"] == 1
    assert level_info(XP_DIV - 1)["level"] == 1
    assert level_info(XP_DIV)["level"] == 2
    assert level_info(XP_DIV * 4)["level"] == 3          # 1 + floor(sqrt(4)) = 3
    half = level_info(XP_DIV // 2)                         # level 1, půlka do dalšího
    assert half["level"] == 1 and half["pct"] == 50
    big = level_info(XP_DIV * 10000)                       # 1 + floor(sqrt(10000)) = 101
    assert big["level"] == 101 and 0 <= big["pct"] <= 100


def test_add_points_feeds_earned_total(client):
    from app.db import get_conn, now_iso
    from app.deps import add_points
    conn = get_conn()
    try:
        u = f"xp_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (u, u, "user", now_iso())).lastrowid
        add_points(conn, uid, 500, "zisk")        # +500 → earned 500
        add_points(conn, uid, -200, "útrata")     # útrata earned NEsnižuje
        add_points(conn, uid, 100, "zisk2")        # +100 → earned 600
        conn.commit()
        r = conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
        assert r["points"] == 400                  # 500 - 200 + 100
        assert r["earned_total"] == 600            # jen kladné: 500 + 100
    finally:
        conn.close()


def test_auth_me_exposes_level(client):
    from app.db import get_conn, now_iso
    from app.config import SESSION_COOKIE
    from app.deps import XP_DIV
    from datetime import datetime, timezone, timedelta
    et = XP_DIV * 4                                         # → level 1 + floor(sqrt(4)) = 3 (XP_DIV-relativní)
    conn = get_conn()
    try:
        u = f"xp_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, earned_total, created_at) VALUES (?,?,?,0,?,?)",
            (u, u, "user", et, now_iso())).lastrowid
        t = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (t, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
    finally:
        conn.close()
    me = client.get("/api/auth/me", headers={"Cookie": f"{SESSION_COOKIE}={t}"}).json()["user"]
    assert me["level"] == 3 and me["earned_total"] == et and 0 <= me["level_pct"] <= 100


def test_add_points_xp_false_skips_earned(client):
    """Admin grant (xp=False) přidá body, ale NE do earned_total (žádný level up za darované body)."""
    from app.db import get_conn, now_iso
    from app.deps import add_points
    conn = get_conn()
    try:
        u = f"xp_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, earned_total, created_at) "
            "VALUES (?,?,?,0,0,?)", (u, u, "user", now_iso())).lastrowid
        add_points(conn, uid, 5000, "Úprava adminem", xp=False)
        conn.commit()
        r = conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
        assert r["points"] == 5000 and r["earned_total"] == 0   # body +5000, XP/level beze změny
    finally:
        conn.close()
