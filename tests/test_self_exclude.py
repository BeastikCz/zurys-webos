"""Sebevyloučení ze sázek (Tipsport-style): zámek na sázkových endpointech, nejde zkrátit/zrušit.

    .venv/Scripts/python.exe -m pytest tests/test_self_exclude.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso
from app.deps import self_excluded_until


def _mk(points: int = 10000):
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", "user", points, now_iso()))
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return cur.lastrowid, tok
    finally:
        conn.close()


def _hdr(tok):
    return {"Cookie": f"{SESSION_COOKIE}={tok}"}


def _row(uid):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()


def _set(uid, val):
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET gamble_block_until = ? WHERE id = ?", (val, uid))
        conn.commit()
    finally:
        conn.close()


def test_self_excluded_until_helper():
    uid, _ = _mk()
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _set(uid, future)
    assert self_excluded_until(_row(uid)) == future
    _set(uid, past)
    assert self_excluded_until(_row(uid)) is None          # vypršelo → může zase sázet
    _set(uid, "permanent")
    assert self_excluded_until(_row(uid)) == "permanent"
    _set(uid, None)
    assert self_excluded_until(_row(uid)) is None


def test_set_endpoint_cannot_shorten(client):
    uid, tok = _mk()
    assert client.post("/api/me/self-exclude", json={"duration": "7d"}, headers=_hdr(tok)).status_code == 200
    assert self_excluded_until(_row(uid)) is not None
    r = client.post("/api/me/self-exclude", json={"duration": "1d"}, headers=_hdr(tok))   # zkrátit nelze
    assert r.status_code == 400 and "prodlouž" in r.json()["detail"].lower()
    assert client.post("/api/me/self-exclude", json={"duration": "30d"}, headers=_hdr(tok)).status_code == 200  # prodloužit lze


def _login_role(role: str):
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
                           (f"{role}_{suf}", f"{role}_{suf}", role, now_iso()))
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return cur.lastrowid, tok
    finally:
        conn.close()


def test_admin_can_block_and_unblock_anyone(client):
    victim, _ = _mk(points=5000)
    _, atok = _login_role("admin")
    assert client.post(f"/api/admin/users/{victim}/gamble-block", json={"duration": "30d"}, headers=_hdr(atok)).status_code == 200
    assert self_excluded_until(_row(victim)) is not None
    # admin override: může i odemknout (běžný user by nemohl)
    assert client.post(f"/api/admin/users/{victim}/gamble-block", json={"duration": "off"}, headers=_hdr(atok)).status_code == 200
    assert self_excluded_until(_row(victim)) is None
    # běžný user na admin endpoint nesmí
    _, utok = _login_role("user")
    assert client.post(f"/api/admin/users/{victim}/gamble-block", json={"duration": "7d"}, headers=_hdr(utok)).status_code == 403


def test_gambling_blocked_and_allowed(client):
    # zamčený → duel create 403, body se nestrhnou
    uid, tok = _mk(points=10000)
    client.post("/api/me/self-exclude", json={"duration": "7d"}, headers=_hdr(tok))
    rd = client.post("/api/games/duels/create", json={"type": "coinflip", "stake": 100}, headers=_hdr(tok))
    assert rd.status_code == 403 and "vylouč" in rd.json()["detail"].lower()
    assert _row(uid)["points"] == 10000
    # nezamčený → duel create projde (gate nerozbíjí normální flow)
    uid2, tok2 = _mk(points=10000)
    ok = client.post("/api/games/duels/create", json={"type": "coinflip", "stake": 100}, headers=_hdr(tok2))
    assert ok.status_code == 200, ok.text
