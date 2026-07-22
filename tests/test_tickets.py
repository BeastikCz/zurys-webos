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
    assert client.get(f"/api/tickets/{ticket_id}", headers=user).json()["events"][0]["event"] == "created"
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
    assert any(e["event"] == "status" for e in thread["events"])

    assert client.post(f"/api/tickets/admin/{ticket_id}/status/closed", headers=admin).status_code == 200
    closed_reply = client.post(f"/api/tickets/{ticket_id}/reply", json={"body": "Ještě něco."}, headers=user)
    assert closed_reply.status_code == 409

    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM dm_messages WHERE user_id=?", (uid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_market_sell_ticket_category(client):
    _, user = _session()
    response = client.post("/api/tickets", json={
        "category": "market", "subcategory": "sell", "subject": "AK-47 Redline (FT)",
        "body": "Float 0.21, požadovaná cena 10 000 sedláků, inspect link přiložím.",
    }, headers=user)
    assert response.status_code == 200


def test_resolve_own_user_ctx_and_autoclose(client):
    uid, user = _session()
    _, stranger = _session()
    _, admin = _session("admin")
    payload = {"category": "orders", "subcategory": "refund", "subject": "Chybí body",
               "body": "Nedorazily body za objednávku."}
    ticket_id = client.post("/api/tickets", json=payload, headers=user).json()["id"]

    # user_ctx jen v admin pohledu
    detail = client.get(f"/api/tickets/admin/{ticket_id}", headers=admin).json()
    assert detail["user_ctx"]["id"] == uid
    assert "level" in detail["user_ctx"] and "orders" in detail["user_ctx"]
    assert "user_ctx" not in client.get(f"/api/tickets/{ticket_id}", headers=user).json()

    # resolve: cizí ticket 404, vlastní OK, druhý pokus 409
    assert client.post(f"/api/tickets/{ticket_id}/resolve", headers=stranger).status_code == 404
    assert client.post(f"/api/tickets/{ticket_id}/resolve", headers=user).status_code == 200
    assert client.get(f"/api/tickets/{ticket_id}", headers=user).json()["ticket"]["status"] == "resolved"
    assert client.post(f"/api/tickets/{ticket_id}/resolve", headers=user).status_code == 409

    # autoclose: resolved starší 7 dnů se při načtení seznamu zavře
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    conn = get_conn()
    try:
        conn.execute("UPDATE support_tickets SET updated_at=? WHERE id=?", (old, ticket_id))
        conn.commit()
    finally:
        conn.close()
    mine = client.get("/api/tickets/mine", headers=user).json()
    assert next(r for r in mine if r["id"] == ticket_id)["status"] == "closed"


def test_attach_and_notify(client):
    uid, user = _session()
    _, stranger = _session()
    _, admin = _session("admin")
    ticket_id = client.post("/api/tickets", json={"category": "web", "subcategory": "bug",
                            "subject": "Bug se screenshotem", "body": "Viz obrázek."}, headers=user).json()["id"]

    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
           "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    assert client.post(f"/api/tickets/{ticket_id}/attach", json={"data": png}, headers=stranger).status_code == 404
    r = client.post(f"/api/tickets/{ticket_id}/attach", json={"data": png}, headers=user)
    assert r.status_code == 200 and r.json()["url"].startswith("/uploads/ticket_")
    assert client.post(f"/api/tickets/{ticket_id}/attach", json={"data": "data:image/png;base64,xx"},
                       headers=user).status_code == 400

    thread = client.get(f"/api/tickets/{ticket_id}", headers=user).json()
    assert thread["messages"][-1]["image"] == r.json()["url"]

    # odpověď admina → in-app notifikace uživateli s odkazem na ticket
    client.post(f"/api/tickets/admin/{ticket_id}/reply", json={"body": "Díky, mrknu."}, headers=admin)
    conn = get_conn()
    try:
        n = conn.execute("SELECT title, link FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1",
                         (uid,)).fetchone()
    finally:
        conn.close()
    assert n and f"#{ticket_id}" in n[0] and n[1] == f"#/podpora/{ticket_id}"

    # ruční resolved od admina → další notifikace
    client.post(f"/api/tickets/admin/{ticket_id}/status/resolved", headers=admin)
    conn = get_conn()
    try:
        n2 = conn.execute("SELECT title FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    finally:
        conn.close()
    assert "vyřešený" in n2[0]


def test_admin_notes_waiting_flag_and_ticket_history(client):
    uid, user = _session()
    _, broadcaster = _session("broadcaster")
    _, admin = _session("admin")
    payload = {"category": "web", "subcategory": "bug", "subject": "První ticket", "body": "Popis."}
    first_id = client.post("/api/tickets", json=payload, headers=user).json()["id"]
    ticket_id = client.post("/api/tickets", json={**payload, "subject": "Druhý ticket"},
                            headers=user).json()["id"]

    # last_from_user: po vytvoření čeká na podporu, po odpovědi admina ne
    row = next(r for r in client.get("/api/tickets/admin/all", headers=admin).json() if r["id"] == ticket_id)
    assert row["last_from_user"] == 1
    client.post(f"/api/tickets/admin/{ticket_id}/reply", json={"body": "Řeším."}, headers=admin)
    row = next(r for r in client.get("/api/tickets/admin/all", headers=admin).json() if r["id"] == ticket_id)
    assert row["last_from_user"] == 0

    # interní poznámka: jen admin; vidí ji admin, uživatel nikdy
    assert client.post(f"/api/tickets/admin/{ticket_id}/note", json={"body": "Interní: čekám na Kick API."},
                       headers=broadcaster).status_code == 403
    assert client.post(f"/api/tickets/admin/{ticket_id}/note", json={"body": "Interní: čekám na Kick API."},
                       headers=admin).status_code == 200
    admin_events = client.get(f"/api/tickets/admin/{ticket_id}", headers=admin).json()["events"]
    assert any(e["event"] == "note" and "Kick API" in e["detail"] for e in admin_events)
    user_events = client.get(f"/api/tickets/{ticket_id}", headers=user).json()["events"]
    assert not any(e["event"] == "note" for e in user_events)

    # user_ctx obsahuje předchozí tickety uživatele (bez toho otevřeného)
    ctx = client.get(f"/api/tickets/admin/{ticket_id}", headers=admin).json()["user_ctx"]
    history_ids = [t["id"] for t in ctx["tickets"]]
    assert first_id in history_ids and ticket_id not in history_ids
    assert {"id", "subject", "status", "created_at"} <= set(ctx["tickets"][0].keys())


def test_ticket_refund_is_atomic_and_visible(client):
    uid, user = _session()
    _, admin = _session("admin")
    ticket_id = client.post("/api/tickets", json={"category": "orders", "subcategory": "refund",
                            "subject": "Refund", "body": "Prosím vrátit body."}, headers=user).json()["id"]
    r = client.post(f"/api/tickets/admin/{ticket_id}/refund", json={"amount": 450}, headers=admin)
    assert r.status_code == 200 and r.json()["balance"] == 450
    detail = client.get(f"/api/tickets/{ticket_id}", headers=user).json()
    assert any(e["event"] == "refund" and "450" in e["detail"] for e in detail["events"])
    conn = get_conn()
    try:
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 450
        assert conn.execute("SELECT COUNT(*) FROM admin_audit WHERE action='user.points' AND details LIKE ?",
                            (f"%ticket #{ticket_id}%",)).fetchone()[0] == 1
    finally:
        conn.close()
