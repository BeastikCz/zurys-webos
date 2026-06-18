import secrets

import pytest
from fastapi import HTTPException

from app.db import get_conn, now_iso, set_setting
from app.deps import check_wager_limit


def _reset_cap(conn):
    set_setting(conn, "eco_wager_cap", "75000")
    conn.commit()


def _make_user(conn, role="user", limit=None):
    uname = f"wg_{secrets.token_hex(4)}"
    cur = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, wager_limit, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (uname, uname, role, 100000, limit, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def _user(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def test_global_wager_cap_blocks_daily_overflow(client):
    conn = get_conn()
    try:
        set_setting(conn, "eco_wager_cap", "100")
        uid = _make_user(conn)

        check_wager_limit(conn, _user(conn, uid), 60)
        with pytest.raises(HTTPException) as exc:
            check_wager_limit(conn, _user(conn, uid), 50)

        assert exc.value.status_code == 403
        assert "Denn" in exc.value.detail
    finally:
        _reset_cap(conn)
        conn.close()


def test_global_wager_cap_is_lower_than_user_limit(client):
    conn = get_conn()
    try:
        set_setting(conn, "eco_wager_cap", "100")
        uid = _make_user(conn, limit=1000)

        with pytest.raises(HTTPException):
            check_wager_limit(conn, _user(conn, uid), 101)
    finally:
        _reset_cap(conn)
        conn.close()


def test_admin_bypasses_global_wager_cap(client):
    conn = get_conn()
    try:
        set_setting(conn, "eco_wager_cap", "100")
        uid = _make_user(conn, role="admin")

        check_wager_limit(conn, _user(conn, uid), 1000)
        row = conn.execute("SELECT wagered_today FROM users WHERE id=?", (uid,)).fetchone()
        assert row["wagered_today"] == 1000
    finally:
        _reset_cap(conn)
        conn.close()


def test_exempt_uid_bypasses_global_wager_cap(client):
    """Ručně whitelistnutá uid (eco_wager_exempt_uids) obejde globální strop jako admin."""
    import json
    conn = get_conn()
    try:
        set_setting(conn, "eco_wager_cap", "100")
        uid = _make_user(conn)                              # běžný user (ne admin)
        set_setting(conn, "eco_wager_exempt_uids", json.dumps([uid]))
        conn.commit()

        check_wager_limit(conn, _user(conn, uid), 1000)     # 1000 >> strop 100, ale výjimka → projde
        row = conn.execute("SELECT wagered_today FROM users WHERE id=?", (uid,)).fetchone()
        assert row["wagered_today"] == 1000

        # bez whitelistu by stejná sázka spadla
        other = _make_user(conn)
        with pytest.raises(HTTPException):
            check_wager_limit(conn, _user(conn, other), 1000)
    finally:
        set_setting(conn, "eco_wager_exempt_uids", "")
        _reset_cap(conn)
        conn.commit()
        conn.close()
