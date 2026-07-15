"""Samostatný support flow: vytvoření, soukromí, odpověď a uzavření."""
import secrets
from datetime import datetime, timedelta, timezone

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _session(role="user"):
    conn = get_conn()
    try:
        name = f"ticket_{role}_{secrets.token_hex(4)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (name, name, role, 0, now_iso()),
        ).lastrowid
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
            (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
        )
        conn.commit()
        return uid, {"Cookie": f"{SESSION_COOKIE}={token}"}
    finally:
        conn.close()


def test_ticket_flow_is_separate_and_private(client):
    uid, user = _session()
    _, stranger = _session()
    _, broadcaster = _session("broadcaster")
    _, admin = _session("admin")
    payload = {"category": "web", "subcategory": "bug", "subject": "Rozbitý košík",
               "body": "Po kliknutí se košík neotevře."}

    created = client.post("/api/tickets", json=payload, headers=user)
    assert created.status_code == 200
    ticket_id = created.json()["id"]
    assert client.get(f"/api/tickets/{ticket_id}", headers=stranger).status_code == 404
    assert client.get("/api/tickets/admin/all", headers=broadcaster).status_code == 403
    assert client.get(f"/api/tickets/admin/{ticket_id}", headers=broadcaster).status_code == 403
    assert client.post(f"/api/tickets/admin/{ticket_id}/reply", json={"body": "Ne."}, headers=broadcaster).status_code == 403
    assert client.post(f"/api/tickets/admin/{ticket_id}/status/resolved", headers=broadcaster).status_code == 403
    assert client.get("/api/tickets/unread", headers=broadcaster).json() == {"count": 0}

    inbox = client.get("/api/tickets/admin/all", headers=admin).json()
    ticket = next(row for row in inbox if row["id"] == ticket_id)
    assert ticket["status"] == "open" and ticket["unread"] == 1

    assert client.post(f"/api/tickets/admin/{ticket_id}/reply", json={"body": "Prověřujeme to."}, headers=admin).status_code == 200
    thread = client.get(f"/api/tickets/{ticket_id}", headers=user).json()
    assert thread["ticket"]["status"] == "in_progress"
    assert [message["body"] for message in thread["messages"]] == [payload["body"], "Prověřujeme to."]

    assert client.post(f"/api/tickets/admin/{ticket_id}/status/closed", headers=admin).status_code == 200
    closed_reply = client.post(f"/api/tickets/{ticket_id}/reply", json={"body": "Ještě něco."}, headers=user)
    assert closed_reply.status_code == 409

    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM dm_messages WHERE user_id=?", (uid,)).fetchone()[0] == 0
    finally:
        conn.close()
