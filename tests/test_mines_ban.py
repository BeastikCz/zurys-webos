"""Cílený ban na Mines: admin zabaní uživatele JEN ve hře Mines, zbytek webu mu jede.

    .venv/Scripts/python.exe -m pytest tests/test_mines_ban.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _session(conn, role="user", points=0):
    from app.db import now_iso
    u = f"{role}_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
        (u, u, role, points, now_iso())).lastrowid
    t = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (t, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit()
    return uid, u, t


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def test_mines_ban_blocks_only_mines(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        _, _, admin_t = _session(conn, "admin")
        _, uname, ut = _session(conn, "user", points=1000)
    finally:
        conn.close()

    # ban přes admin endpoint
    r = client.post("/api/admin/mines-ban", json={"username": uname, "banned": True}, headers=_hdr(admin_t))
    assert r.status_code == 200, r.text
    assert r.json()["banned"] is True and r.json()["total_banned"] >= 1

    # Mines start → 403 (zabanovaný)
    r = client.post("/api/mines/start", json={"bet": 100, "mines": 4}, headers=_hdr(ut))
    assert r.status_code == 403, f"zabanovaný nesmí spustit Mines, je {r.status_code}: {r.text}"

    # zbytek webu mu jede – sanity: auth/me projde
    assert client.get("/api/auth/me", headers=_hdr(ut)).status_code == 200

    # odban → start zase projde
    r = client.post("/api/admin/mines-ban", json={"username": uname, "banned": False}, headers=_hdr(admin_t))
    assert r.status_code == 200 and r.json()["banned"] is False
    r = client.post("/api/mines/start", json={"bet": 100, "mines": 4}, headers=_hdr(ut))
    assert r.status_code == 200, f"po odbanu má Mines jít, je {r.status_code}: {r.text}"


def test_mines_ban_unknown_user_404(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        _, _, admin_t = _session(conn, "admin")
    finally:
        conn.close()
    r = client.post("/api/admin/mines-ban", json={"username": "nikdo_xyz_404"}, headers=_hdr(admin_t))
    assert r.status_code == 404


def test_mines_bans_list(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        _, _, admin_t = _session(conn, "admin")
        _, uname, _ = _session(conn, "user")
    finally:
        conn.close()
    client.post("/api/admin/mines-ban", json={"username": uname, "banned": True}, headers=_hdr(admin_t))
    d = client.get("/api/admin/mines-bans", headers=_hdr(admin_t)).json()
    assert any(b["username"] == uname for b in d["banned"]), "zabanovaný má být v seznamu"
