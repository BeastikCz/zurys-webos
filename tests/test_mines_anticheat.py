"""Persistent Mines throttling, behavior detection and expiring bans."""
import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException


def _user(conn):
    from app.db import now_iso
    name = f"mac_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username,username,role,points,created_at) VALUES (?,?,?,?,?)",
        (name, name, "user", 100000, now_iso()),
    ).lastrowid
    conn.commit()
    return uid


def _game(conn, uid, created, duration=0.5):
    ended = created + timedelta(seconds=duration)
    conn.execute(
        "INSERT INTO mines_games (user_id,bet,mines,layout,revealed,status,payout,created_at,ended_at) "
        "VALUES (?,?,?,?,?,'busted',0,?,?)",
        (uid, 100, 24, "[0]", "[]", created.isoformat(), ended.isoformat()),
    )


def test_scan_uses_real_five_minute_window(client):
    from app.db import get_conn
    from app import mines_anticheat
    conn = get_conn()
    try:
        uid = _user(conn)
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        for i in range(50):
            _game(conn, uid, old + timedelta(seconds=i))
        conn.commit()
        mines_anticheat._scan(conn)
        assert not mines_anticheat.is_mines_banned(conn, uid)
    finally:
        conn.close()


def test_fast_volume_gets_expiring_auto_ban(client):
    from app.db import get_conn
    from app import mines_anticheat
    conn = get_conn()
    try:
        uid = _user(conn)
        start = datetime.now(timezone.utc) - timedelta(minutes=4)
        for i in range(mines_anticheat.BOT_THRESHOLD):
            _game(conn, uid, start + timedelta(seconds=i * 3), duration=0.4)
        conn.commit()
        mines_anticheat._scan(conn)
        assert mines_anticheat.is_mines_banned(conn, uid)
        assert mines_anticheat.mines_ban_expiries(conn).get(str(uid))
    finally:
        conn.close()


def test_slow_volume_does_not_auto_ban(client):
    from app.db import get_conn
    from app import mines_anticheat
    conn = get_conn()
    try:
        uid = _user(conn)
        start = datetime.now(timezone.utc) - timedelta(minutes=4)
        for i in range(mines_anticheat.BOT_THRESHOLD):
            _game(conn, uid, start + timedelta(seconds=i * 3), duration=2.5)
        conn.commit()
        mines_anticheat._scan(conn)
        assert not mines_anticheat.is_mines_banned(conn, uid)
    finally:
        conn.close()


def test_persistent_start_limits(client):
    from app.db import get_conn
    from app import mines_anticheat
    conn = get_conn()
    try:
        uid = _user(conn)
        now = datetime.now(timezone.utc)
        for i in range(mines_anticheat.START_LIMIT_1M):
            _game(conn, uid, now - timedelta(seconds=59 - i * 4), duration=2)
        conn.commit()
        with pytest.raises(HTTPException) as exc:
            mines_anticheat.check_start_allowed(conn, uid, now)
        assert exc.value.status_code == 429
    finally:
        conn.close()


def test_expired_auto_ban_cleans_itself(client):
    from app.db import get_conn, set_setting
    from app import mines_anticheat
    conn = get_conn()
    try:
        uid = _user(conn)
        conn.execute("UPDATE users SET ban_reason=? WHERE id=?", (mines_anticheat.BAN_REASON, uid))
        set_setting(conn, mines_anticheat.BAN_IDS_KEY, json.dumps([uid]))
        set_setting(conn, mines_anticheat.BAN_EXPIRES_KEY,
                    json.dumps({str(uid): (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}))
        conn.commit()
        assert not mines_anticheat.is_mines_banned(conn, uid)
        reason = conn.execute("SELECT ban_reason FROM users WHERE id=?", (uid,)).fetchone()["ban_reason"]
        assert reason is None
    finally:
        conn.close()
