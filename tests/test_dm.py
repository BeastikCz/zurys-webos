"""Soukromé zprávy zůstávají oddělené od support ticketů."""
import secrets
from datetime import datetime, timedelta, timezone

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _session(role):
    conn = get_conn()
    try:
        name = f"dm_{role}_{secrets.token_hex(4)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (name, name, role, 0, now_iso()),
        ).lastrowid
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
        )
        conn.commit()
        return uid, {"Cookie": f"{SESSION_COOKIE}={token}"}
    finally:
        conn.close()


def test_user_cannot_start_private_message(client):
    uid, user_headers = _session("user")
    _, admin_headers = _session("admin")

    blocked = client.post("/api/dm/reply", json={"body": "Chci založit ticket."}, headers=user_headers)
    assert blocked.status_code == 403
    assert client.post(f"/api/dm/send/{uid}", json={"body": "Soukromá zpráva týmu."}, headers=admin_headers).status_code == 200
    assert client.post("/api/dm/reply", json={"body": "Odpověď uživatele."}, headers=user_headers).status_code == 200
    thread = client.get("/api/dm/thread", headers=user_headers).json()
    assert [message["body"] for message in thread["messages"]] == [
        "Soukromá zpráva týmu.", "Odpověď uživatele."
    ]
    assert thread["can_reply"] is True
