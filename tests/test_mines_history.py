"""Admin Mines historie: house staty + feed (filtr dle nicku) + top hráči.

    .venv/Scripts/python.exe -m pytest tests/test_mines_history.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _admin_token():
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        u = f"adm_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (u, u, "admin", 0, now_iso())).lastrowid
        t = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (t, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return t
    finally:
        conn.close()


def test_mines_history(client):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        u = f"mh_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (u, u, "user", 0, now_iso())).lastrowid
        # cashed: bet 100, payout 300 (net +200, 3 odkrytá pole) + busted: bet 100 (net −100)
        conn.execute("INSERT INTO mines_games (user_id,bet,mines,layout,revealed,status,payout,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (uid, 100, 4, "[]", "[1,2,3]", "cashed", 300, now_iso()))
        conn.execute("INSERT INTO mines_games (user_id,bet,mines,layout,revealed,status,payout,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (uid, 100, 4, "[]", "[]", "busted", 0, now_iso()))
        conn.commit()
    finally:
        conn.close()

    d = client.get(f"/api/admin/mines-history?q={u}", headers={"Cookie": f"{SESSION_COOKIE}={_admin_token()}"}).json()
    assert d["stats"]["games"] >= 2
    mine = [f for f in d["feed"] if f["username"] == u]
    assert len(mine) == 2, "feed má vrátit obě hry toho hráče"
    cashed = [f for f in mine if f["status"] == "cashed"][0]
    assert cashed["net"] == 200 and cashed["safe"] == 3
    assert isinstance(d["winners"], list) and isinstance(d["losers"], list)


def test_mines_history_requires_admin(client):
    # bez session → 401
    assert client.get("/api/admin/mines-history").status_code == 401
