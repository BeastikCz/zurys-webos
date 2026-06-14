"""Notifikační centrum: serverové události vytvoří notifikaci + endpointy unread/read.

    .venv/Scripts/python.exe -m pytest tests/test_notifications.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

import pytest

from app.config import SESSION_COOKIE


@pytest.fixture(autouse=True)
def _enable_gifts(monkeypatch):
    monkeypatch.setattr("app.routers.misc.GIFT_ENABLED", True)


def _user(role="user", points=10000, age_hours=500):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        u = f"{role}_{secrets.token_hex(4)}"
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (u, u, role, points, created))
        uid = cur.lastrowid
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return tok, u, uid
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def _pending_gift_id(to_id):
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute("SELECT id FROM gift_requests WHERE to_user_id=? AND status='pending'",
                            (to_id,)).fetchone()["id"]
    finally:
        conn.close()


def test_gift_approve_notifies_both(client):
    s_tok, _s, _sid = _user(points=5000)
    r_tok, r_name, r_id = _user(points=0)
    a_tok, _a, _aid = _user("admin")
    assert client.post("/api/exchange/gift", json={"username": r_name, "amount": 700},
                       headers=_hdr(s_tok)).status_code == 200
    gid = _pending_gift_id(r_id)
    assert client.post(f"/api/admin/gift-requests/{gid}/approve", headers=_hdr(a_tok)).status_code == 200
    s_n = client.get("/api/notifications", headers=_hdr(s_tok)).json()
    r_n = client.get("/api/notifications", headers=_hdr(r_tok)).json()
    assert s_n["unread"] >= 1 and any("schvál" in i["title"].lower() for i in s_n["items"])
    assert r_n["unread"] >= 1 and any("dar" in i["title"].lower() for i in r_n["items"])


def test_gift_reject_notifies_sender(client):
    s_tok, _s, _sid = _user(points=3000)
    r_tok, r_name, r_id = _user(points=0)
    a_tok, _a, _aid = _user("admin")
    assert client.post("/api/exchange/gift", json={"username": r_name, "amount": 500},
                       headers=_hdr(s_tok)).status_code == 200
    gid = _pending_gift_id(r_id)
    assert client.post(f"/api/admin/gift-requests/{gid}/reject", headers=_hdr(a_tok)).status_code == 200
    s_n = client.get("/api/notifications", headers=_hdr(s_tok)).json()
    assert any("zamít" in i["title"].lower() for i in s_n["items"])


def test_unread_and_mark_read(client):
    s_tok, _s, _sid = _user(points=2000)
    r_tok, r_name, r_id = _user(points=0)
    a_tok, _a, _aid = _user("admin")
    client.post("/api/exchange/gift", json={"username": r_name, "amount": 200}, headers=_hdr(s_tok))
    gid = _pending_gift_id(r_id)
    client.post(f"/api/admin/gift-requests/{gid}/approve", headers=_hdr(a_tok))
    assert client.get("/api/notifications/unread", headers=_hdr(s_tok)).json()["count"] >= 1
    assert client.post("/api/notifications/read", headers=_hdr(s_tok)).json()["ok"] is True
    assert client.get("/api/notifications/unread", headers=_hdr(s_tok)).json()["count"] == 0
