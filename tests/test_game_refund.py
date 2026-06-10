"""Admin refund dohrané 1v1 hry: storno výhry vítězi + vrácení vkladu oběma.

    .venv/Scripts/python.exe -m pytest tests/test_game_refund.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso
from app.routers import games


def _login(role: str) -> str:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"{role}_{suf}", f"{role}_{suf}", role, now_iso()))
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def _mkuser(points: int) -> int:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", "user", points, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _bal(uid: int) -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()


def _finished_game(p1, p2, winner) -> int:
    conn = get_conn()
    try:
        gid = conn.execute(
            "INSERT INTO games (type,status,stake,board,turn,p1_id,p2_id,winner,move_count,created_at,updated_at) "
            "VALUES ('gomoku','finished',100,?,1,?,?,?,9,?,?)",
            (games._empty_board(), p1, p2, winner, now_iso(), now_iso())).lastrowid
        conn.commit()
        return gid
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def test_refund_finished_game_moves_stake_back(client):
    """Refund: vítěz −stake (storno výhry, rake 0 → prize 200, +vklad 100), poražený +stake."""
    p1, p2 = _mkuser(1000), _mkuser(1000)
    gid = _finished_game(p1, p2, winner=1)   # vyhrál p1
    b1, b2 = _bal(p1), _bal(p2)
    r = client.post(f"/api/admin/games/{gid}/refund", headers=_hdr(_login("admin")))
    assert r.status_code == 200 and r.json().get("ok"), r.text
    assert _bal(p1) == b1 - 100, "vítěz přijde o čistý zisk (storno výhry + vrácený vklad = −stake)"
    assert _bal(p2) == b2 + 100, "poražený dostane vklad zpět"
    conn = get_conn()
    try:
        assert conn.execute("SELECT status FROM games WHERE id=?", (gid,)).fetchone()[0] == "cancelled"
    finally:
        conn.close()


def test_refund_rejects_draw(client):
    p1, p2 = _mkuser(1000), _mkuser(1000)
    gid = _finished_game(p1, p2, winner=0)   # remíza
    r = client.post(f"/api/admin/games/{gid}/refund", headers=_hdr(_login("admin")))
    assert r.status_code == 200 and not r.json().get("ok"), "remízu nejde refundnout (vklady už vráceny)"


def test_history_lists_finished(client):
    p1, p2 = _mkuser(1000), _mkuser(1000)
    gid = _finished_game(p1, p2, winner=1)
    r = client.get("/api/admin/games/history", headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text
    assert any(h["id"] == gid and h["kind"] == "Piškvorky" and h["refundable"] for h in r.json())
