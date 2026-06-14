"""Auto-úklid objednávek: daemon maže staré VYŘÍZENÉ, nechává čerstvé i čekající.

    .venv/Scripts/python.exe -m pytest tests/test_order_cleanup.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta


def _mk_order(conn, uid, status, age_days):
    created = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    conn.execute(
        "INSERT INTO orders (user_id, product_id, points_spent, status, created_at) VALUES (?,?,?,?,?)",
        (uid, None, 100, status, created))


def test_purge_old_fulfilled_only(client):
    from app.db import get_conn
    from app import order_cleanup
    conn = get_conn()
    try:
        u = f"oc_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (u, u, "user", 0, datetime.now(timezone.utc).isoformat())).lastrowid
        _mk_order(conn, uid, "fulfilled", 40)    # starý vyřízený  → SMAZAT
        _mk_order(conn, uid, "fulfilled", 5)     # čerstvý vyřízený → nechat
        _mk_order(conn, uid, "pending", 100)     # starý čekající   → NECHAT (nevyřízené se nemažou)
        conn.commit()
    finally:
        conn.close()

    n = order_cleanup._run_once()
    assert n >= 1, "měl smazat aspoň ten starý vyřízený"

    conn = get_conn()
    try:
        rows = conn.execute("SELECT status FROM orders WHERE user_id = ?", (uid,)).fetchall()
    finally:
        conn.close()
    statuses = sorted(r["status"] for r in rows)
    assert "pending" in statuses, "čekající objednávka se NESMÍ smazat"
    assert statuses.count("fulfilled") == 1, "starý vyřízený pryč, čerstvý zůstává"
