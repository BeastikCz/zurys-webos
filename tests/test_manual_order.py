"""Ruční ticket/objednávka v adminu (kompenzace za bug).

Politika:
  * Vytvořit ruční ticket smí JEN broadcaster + admin (mod NE – je to grant-like akce).
  * Ticket NEÚČTUJE body uživateli – jen založí záznam k vyřízení (status 'pending').
  * Neznámý uživatel → 404.

    .venv/Scripts/python.exe -m pytest tests/test_manual_order.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _login_as(role: str) -> str:
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


def _make_target(points: int = 100):
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        uname = f"tgt_{suf}"
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (uname, uname, "user", points, now_iso()))
        conn.commit()
        return cur.lastrowid, uname
    finally:
        conn.close()


def _hdr(token):
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def test_admin_creates_ticket_without_charging_points(client):
    uid, uname = _make_target(points=100)
    r = client.post("/api/admin/orders",
                    json={"username": uname, "product_name": "Nuz - kompenzace za bug", "points_spent": 500},
                    headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    oid = r.json()["id"]
    # objednávka je v přehledu a čeká
    lst = client.get("/api/admin/orders?status=pending", headers=_hdr(_login_as("admin"))).json()
    assert any(o["id"] == oid and o["username"] == uname and o["status"] == "pending" for o in lst), \
        "ticket musí být v přehledu jako 'pending'"
    # body uživatele se NEZMĚNILY
    conn = get_conn()
    try:
        pts = conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()
    assert pts == 100, "ruční ticket nesmí měnit body uživatele"


def test_broadcaster_can_create(client):
    _, uname = _make_target()
    r = client.post("/api/admin/orders", json={"username": uname, "product_name": "x"},
                    headers=_hdr(_login_as("broadcaster")))
    assert r.status_code == 200, r.text


def test_mod_cannot_create(client):
    _, uname = _make_target()
    r = client.post("/api/admin/orders", json={"username": uname, "product_name": "x"},
                    headers=_hdr(_login_as("mod")))
    assert r.status_code == 403, f"BEZPEČNOST: mod nesmí přidávat tickety, dostal {r.status_code}"


def test_unknown_user_404(client):
    r = client.post("/api/admin/orders",
                    json={"username": "neexistuje_" + secrets.token_hex(3), "product_name": "x"},
                    headers=_hdr(_login_as("admin")))
    assert r.status_code == 404


# ---------------- Hromadné tickety ----------------

def test_bulk_creates_multiple(client):
    _, u1 = _make_target()
    _, u2 = _make_target()
    items = [{"username": u1, "product_name": "Odměna A"},
             {"username": u2, "product_name": "Odměna B", "points_spent": 5}]
    r = client.post("/api/admin/orders/bulk", json={"items": items}, headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["created_count"] == 2 and d["error_count"] == 0, d


def test_bulk_reports_bad_lines_but_creates_good(client):
    """Špatný řádek (neznámý user / prázdná odměna) se vrátí jako chyba, dobrý projde."""
    _, u1 = _make_target()
    items = [{"username": u1, "product_name": "OK"},
             {"username": "neexistuje_" + secrets.token_hex(3), "product_name": "B"},
             {"username": u1, "product_name": "   "}]
    r = client.post("/api/admin/orders/bulk", json={"items": items}, headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["created_count"] == 1, d
    assert d["error_count"] == 2, d
    assert d["errors"][0]["line"] == 2


def test_bulk_mod_forbidden(client):
    _, u1 = _make_target()
    r = client.post("/api/admin/orders/bulk", json={"items": [{"username": u1, "product_name": "A"}]},
                    headers=_hdr(_login_as("mod")))
    assert r.status_code == 403, f"mod nesmí hromadné tickety, dostal {r.status_code}"


def test_bulk_empty_400(client):
    r = client.post("/api/admin/orders/bulk", json={"items": []}, headers=_hdr(_login_as("admin")))
    assert r.status_code == 400


# ---------------- Počet ticketů + dopočet ceny ----------------

def test_count_creates_multiple_orders(client):
    """Pole 'count' vytvoří tolik objednávek (ticketů)."""
    _, uname = _make_target()
    r = client.post("/api/admin/orders", json={"username": uname, "product_name": "Odmena X", "count": 3},
                    headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 3
    lst = client.get("/api/admin/orders?status=pending", headers=_hdr(_login_as("admin"))).json()
    mine = [o for o in lst if o["username"] == uname]
    assert len(mine) == 3, f"měly vzniknout 3 objednávky, je {len(mine)}"


def test_points_autofilled_from_product(client):
    """Body objednávky se dopočítají z ceny odměny (uživatel je v ticketu nezadává)."""
    pname = "AutoFill Knife " + secrets.token_hex(3)
    conn = get_conn()
    try:
        conn.execute("INSERT INTO products (name, cost_points, type, active, created_at) VALUES (?,?,?,1,?)",
                     (pname, 777, "instant", now_iso()))
        conn.commit()
    finally:
        conn.close()
    _, uname = _make_target()
    r = client.post("/api/admin/orders", json={"username": uname, "product_name": pname},
                    headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    assert r.json()["points_spent"] == 777, "body se mají dopočítat z ceny odměny"
