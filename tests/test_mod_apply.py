"""Nábor moderátorů: přihláška + admin accept (set_mod → role mod) + duplicita + zavřený nábor.

    .venv/Scripts/python.exe -m pytest tests/test_mod_apply.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE

_APP = {"discord": "tester#1", "motivation": "Chci pomahat drzet chat v pohode a mam cas vecer."}


def _user(role="user", points=0):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        u = f"{role}_{secrets.token_hex(4)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (u, u, role, points, now_iso())).lastrowid
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return tok, u, uid
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def _set_open(val):
    from app.db import get_conn, set_setting
    conn = get_conn()
    try:
        set_setting(conn, "modapp_open", val)
        conn.commit()
    finally:
        conn.close()


def test_apply_then_admin_accept_sets_mod(client):
    _set_open("1")
    tok, uname, uid = _user("user")
    a_tok, _a, _aid = _user("admin")

    assert client.post("/api/mod-apply", json=_APP, headers=_hdr(tok)).status_code == 200
    # duplicitní přihláška (pending) → blok
    assert client.post("/api/mod-apply", json=_APP, headers=_hdr(tok)).status_code == 400

    lst = client.get("/api/admin/mod-applications", headers=_hdr(a_tok)).json()
    mine = [a for a in lst["pending"] if a["username"] == uname]
    assert mine, "admin musí vidět čekající přihlášku"
    assert mine[0]["answers"]["discord"] == "tester#1"
    assert mine[0]["stats"]["role"] == "user"
    aid = mine[0]["id"]

    dec = client.post(f"/api/admin/mod-applications/{aid}/decide",
                      json={"action": "accept", "set_mod": True}, headers=_hdr(a_tok))
    assert dec.status_code == 200

    from app.db import get_conn
    conn = get_conn()
    try:
        role = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()["role"]
        notif = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND title LIKE '%týmu%'",
                            (uid,)).fetchone()["c"]
    finally:
        conn.close()
    assert role == "mod", "po accept+set_mod má být role 'mod'"
    assert notif >= 1, "uchazeč má dostat notifikaci o přijetí"


def test_apply_blocked_when_closed(client):
    _set_open("0")
    tok, _u, _uid = _user("user")
    assert client.post("/api/mod-apply", json=_APP, headers=_hdr(tok)).status_code == 403
    _set_open("1")
