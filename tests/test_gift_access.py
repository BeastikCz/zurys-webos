"""Žádosti o dar (gift-requests): smí spravovat admin + broadcaster, ne mod/divák.

    .venv/Scripts/python.exe -m pytest tests/test_gift_access.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _tok(role):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        u = f"{role}_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (u, u, role, now_iso())).lastrowid
        t = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (t, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return t
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def test_gift_requests_admin_and_broadcaster_ok(client):
    assert client.get("/api/admin/gift-requests", headers=_hdr(_tok("admin"))).status_code == 200
    assert client.get("/api/admin/gift-requests", headers=_hdr(_tok("broadcaster"))).status_code == 200, \
        "broadcaster nově smí spravovat žádosti o dar"


def test_gift_requests_mod_and_user_forbidden(client):
    assert client.get("/api/admin/gift-requests", headers=_hdr(_tok("mod"))).status_code == 403, \
        "mod na žádosti o dar nemá"
    assert client.get("/api/admin/gift-requests", headers=_hdr(_tok("user"))).status_code == 403
