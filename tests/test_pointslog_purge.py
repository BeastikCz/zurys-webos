"""Úklid testovacích pohybů bodů: smazání KONKRÉTNÍCH řádků points_logu podle ID.

Politika:
  * Smí JEN admin (broadcaster ani mod NE – maže se audit-citlivý log pohybů bodů).
  * Pojistka confirm_reason: smaže jen řádky přesně s tím důvodem (chrání reálné pohyby).
  * NEMĚNÍ zůstatky uživatelů – odstraní jen řádky z logu.

    .venv/Scripts/python.exe -m pytest tests/test_pointslog_purge.py -v
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


def _hdr(token):
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _mk_user(points: int = 1000) -> int:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", "user", points, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _mk_log(uid: int, change: int, reason: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
            (uid, change, reason, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_admin_purges_specific_rows(client):
    uid = _mk_user(points=500)
    a = _mk_log(uid, 5_000_000, "TEST")
    b = _mk_log(uid, -5_000_000, "TEST")
    keep = _mk_log(uid, 100, "Sledování streamu")
    r = client.post("/api/admin/economy/points-log/purge",
                    json={"ids": [a, b], "confirm_reason": "TEST"},
                    headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 2
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM points_log WHERE id IN (?,?)", (a, b)).fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM points_log WHERE id=?", (keep,)).fetchone()["c"] == 1, \
            "řádek mimo seznam ID se nesmí dotknout"
        # zůstatek uživatele NEzměněn (mažeme jen log, ne body)
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 500
    finally:
        conn.close()


def test_confirm_reason_guards_mismatched_rows(client):
    """Pojistka: i když ID pošlu, řádek s JINÝM důvodem se NEsmaže (chrání reálné pohyby)."""
    uid = _mk_user()
    test_row = _mk_log(uid, 9999, "TEST")
    real_row = _mk_log(uid, 9999, "Nákup: Nůž")
    r = client.post("/api/admin/economy/points-log/purge",
                    json={"ids": [test_row, real_row], "confirm_reason": "TEST"},
                    headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM points_log WHERE id=?", (real_row,)).fetchone()["c"] == 1, \
            "BEZPEČNOST: řádek s jiným důvodem než confirm_reason se NESMÍ smazat"
        assert conn.execute("SELECT COUNT(*) c FROM points_log WHERE id=?", (test_row,)).fetchone()["c"] == 0
    finally:
        conn.close()


def test_mod_and_broadcaster_forbidden(client):
    uid = _mk_user()
    row = _mk_log(uid, 1, "TEST")
    for role in ("mod", "broadcaster"):
        r = client.post("/api/admin/economy/points-log/purge",
                        json={"ids": [row], "confirm_reason": "TEST"},
                        headers=_hdr(_login_as(role)))
        assert r.status_code == 403, f"BEZPEČNOST: {role} nesmí mazat points_log, dostal {r.status_code}"
    # řádek pořád existuje
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM points_log WHERE id=?", (row,)).fetchone()["c"] == 1
    finally:
        conn.close()


def test_empty_ids_rejected(client):
    r = client.post("/api/admin/economy/points-log/purge",
                    json={"ids": []}, headers=_hdr(_login_as("admin")))
    assert r.status_code == 422, "prázdný seznam ID má padnout na validaci"
