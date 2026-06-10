"""Hromadné „Vše vyřízeno": označí ČEKAJÍCÍ objednávky (volitelně dle položky) jako vyřízené.

    .venv/Scripts/python.exe -m pytest tests/test_fulfill_all.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _login_admin():
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"adm_{suf}", f"adm_{suf}", "admin", now_iso()))
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def _mk_product(name):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO products (name, cost_points, type, active, created_at) VALUES (?,?,?,1,?)",
            (name, 100, "instant", now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _mk_user():
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"u_{suf}", f"u_{suf}", "user", now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _mk_order(uid, pid, status="pending"):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO orders (user_id, product_id, points_spent, status, created_at) VALUES (?,?,?,?,?)",
            (uid, pid, 100, status, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _status(oid):
    conn = get_conn()
    try:
        return conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()["status"]
    finally:
        conn.close()


def test_fulfill_all_only_selected_product(client):
    tok = _login_admin()
    uid = _mk_user()
    pA = _mk_product("FA-A-" + secrets.token_hex(3))
    pB = _mk_product("FA-B-" + secrets.token_hex(3))
    a1, a2 = _mk_order(uid, pA), _mk_order(uid, pA)
    b1 = _mk_order(uid, pB)
    r = client.post(f"/api/admin/orders/fulfill-all?product_id={pA}", headers=_hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["fulfilled"] == 2
    assert _status(a1) == "fulfilled" and _status(a2) == "fulfilled"
    assert _status(b1) == "pending", "BEZPEČNOST: jiná položka se NESMÍ dotknout"


def test_fulfill_all_skips_already_fulfilled(client):
    tok = _login_admin()
    uid = _mk_user()
    pC = _mk_product("FA-C-" + secrets.token_hex(3))
    done = _mk_order(uid, pC, status="fulfilled")
    pend = _mk_order(uid, pC, status="pending")
    r = client.post(f"/api/admin/orders/fulfill-all?product_id={pC}", headers=_hdr(tok))
    assert r.json()["fulfilled"] == 1, "jen ten čekající se počítá"
    assert _status(pend) == "fulfilled"
    assert _status(done) == "fulfilled"
