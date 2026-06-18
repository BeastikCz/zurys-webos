"""Dar v Exchange = ŽÁDOST schvalovaná adminem (escrow flow).

Co se testuje:
  * odeslání daru body HNED zablokuje u odesílatele (escrow) a založí pending žádost,
    příjemce zatím NEdostane nic;
  * admin POVOLÍ → body se připíšou příjemci, escrow řádek se přejmenuje na kanonický
    „Dar pro X 🎁" (aby ho funnel detektor započítal);
  * admin ZAMÍTNE → body se vrátí odesílateli, příjemce nedostane nic;
  * escrow brání dvojí útratě zablokovaných bodů;
  * dvojí rozhodnutí o téže žádosti je odmítnuto.

    .venv/Scripts/python.exe -m pytest tests/test_gift_requests.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

import pytest

from app.config import SESSION_COOKIE


@pytest.fixture(autouse=True)
def _enable_gifts(monkeypatch):
    monkeypatch.setattr("app.routers.misc.GIFT_ENABLED", True)


def _make_user(role: str = "user", age_hours: float = 500, points: int = 10000):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        uname = f"{role}_{secrets.token_hex(4)}"
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (uname, uname, role, points, created))
        uid = cur.lastrowid
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token, uname, uid
    finally:
        conn.close()


def _hdr(token):
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _points(uid):
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()


def _earned(uid):
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute("SELECT earned_total FROM users WHERE id=?", (uid,)).fetchone()["earned_total"]
    finally:
        conn.close()


def _reason(log_id):
    from app.db import get_conn
    conn = get_conn()
    try:
        r = conn.execute("SELECT reason FROM points_log WHERE id=?", (log_id,)).fetchone()
        return r["reason"] if r else None
    finally:
        conn.close()


def _gift(client, token, to_username, amount=1000, note=""):
    return client.post("/api/exchange/gift",
                       json={"username": to_username, "amount": amount, "note": note},
                       headers=_hdr(token))


def _pending(client, admin_token):
    return client.get("/api/admin/gift-requests", headers=_hdr(admin_token)).json()


def test_gift_creates_pending_and_escrows(client):
    """Dar zablokuje body u odesílatele a založí pending žádost; příjemce zatím nic."""
    s_tok, _s, s_id = _make_user(points=10000)
    _r_tok, r_name, r_id = _make_user(points=200)
    resp = _gift(client, s_tok, r_name, 1000, note="díky za pomoc")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("pending") is True
    assert _points(s_id) == 9000, "odesílateli se měly body hned zablokovat (escrow)"
    assert _points(r_id) == 200, "příjemce zatím NEsmí dostat nic"
    pend = _pending(client, _make_user("admin")[0])
    mine = [g for g in pend["pending"] if g["to"] == r_name and g["amount"] == 1000]
    assert mine, "žádost má být v pending"
    assert mine[0]["note"] == "díky za pomoc", "důvod se má uložit a vrátit adminovi"


def test_approve_moves_points_and_canonicalizes(client):
    """Povolení připíše body příjemci a escrow řádek přejmenuje na 'Dar pro X 🎁'."""
    from app.db import get_conn
    s_tok, _s, s_id = _make_user(points=5000)
    _r_tok, r_name, r_id = _make_user(points=0)
    assert _gift(client, s_tok, r_name, 1500).status_code == 200
    conn = get_conn()
    try:
        gr = conn.execute("SELECT id, escrow_log_id FROM gift_requests WHERE to_user_id=? "
                          "AND status='pending'", (r_id,)).fetchone()
    finally:
        conn.close()
    a_tok = _make_user("admin")[0]
    ap = client.post(f"/api/admin/gift-requests/{gr['id']}/approve", headers=_hdr(a_tok))
    assert ap.status_code == 200, ap.text
    assert _points(r_id) == 1500, "příjemce má dostat body po schválení"
    assert _earned(r_id) == 0, "dar nesmi pridat XP do levelu/Battle Passu"
    assert _points(s_id) == 3500, "odesílatel zůstává odečtený (escrow se nevrací)"
    assert _reason(gr["escrow_log_id"]) == f"Dar pro {r_name} 🎁", "escrow řádek se má kanonizovat"
    # druhé rozhodnutí už nesmí projít
    again = client.post(f"/api/admin/gift-requests/{gr['id']}/approve", headers=_hdr(a_tok))
    assert again.status_code == 400


def test_reject_refunds_sender(client):
    """Zamítnutí vrátí body odesílateli; příjemce nedostane nic."""
    from app.db import get_conn
    s_tok, _s, s_id = _make_user(points=4000)
    _r_tok, r_name, r_id = _make_user(points=100)
    assert _gift(client, s_tok, r_name, 2000).status_code == 200
    assert _points(s_id) == 2000
    conn = get_conn()
    try:
        gid = conn.execute("SELECT id FROM gift_requests WHERE to_user_id=? AND status='pending'",
                           (r_id,)).fetchone()["id"]
    finally:
        conn.close()
    a_tok = _make_user("admin")[0]
    rj = client.post(f"/api/admin/gift-requests/{gid}/reject", headers=_hdr(a_tok))
    assert rj.status_code == 200, rj.text
    assert _points(s_id) == 4000, "odesílateli se mají body vrátit"
    assert _points(r_id) == 100, "příjemce nedostane nic"


def test_escrow_prevents_double_spend(client):
    """Zablokované body už nejdou poslat podruhé (escrow odečetl hned)."""
    s_tok, _s, s_id = _make_user(points=1000)
    _r1_tok, r1, _r1id = _make_user(points=0)
    _r2_tok, r2, _r2id = _make_user(points=0)
    assert _gift(client, s_tok, r1, 1000).status_code == 200   # vše zablokováno
    assert _points(s_id) == 0
    second = _gift(client, s_tok, r2, 1)                        # už není z čeho
    assert second.status_code == 400, "po escrow nesmí jít utratit znovu"
