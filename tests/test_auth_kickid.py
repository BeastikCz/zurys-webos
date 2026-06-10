"""Login páruje účet podle stabilního kick_id → změna nicku NEZALOŽÍ druhý účet.

Tohle hlídá bug, kvůli kterému vznikaly duplicitní účty: párování bylo podle nicku, takže
po změně nicku na Kicku login nenašel účet a založil nový (prázdný) → člověk měl 2 účty.

    .venv/Scripts/python.exe -m pytest tests/test_auth_kickid.py -v
"""
import secrets

from app.db import get_conn, now_iso
from app.routers.auth import _find_or_create_kick_user


def _mk(conn, kick_username, kick_id, banned=0, points=0):
    return conn.execute(
        "INSERT INTO users (kick_username, kick_id, username, role, points, banned, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (kick_username, kick_id, kick_username, "user", points, banned, now_iso())).lastrowid


def test_nick_change_keeps_same_account(client):
    """Stejný kick_id, jiný nick → STEJNÝ účet, jen se přejmenuje. Žádný druhý účet."""
    conn = get_conn()
    try:
        kid = "kid_" + secrets.token_hex(5)
        old_nick = "old_" + secrets.token_hex(3)
        new_nick = "new_" + secrets.token_hex(3)
        old_id = _mk(conn, old_nick, kid, points=500)
        conn.commit()
        row = _find_or_create_kick_user(conn, new_nick, new_nick, kick_id=kid)
        assert row["id"] == old_id, "změna nicku musí vrátit STEJNÝ účet (match přes kick_id)"
        assert row["kick_username"] == new_nick, "nick se má aktualizovat na nový"
        assert row["points"] == 500, "body zůstávají na účtu"
        cnt = conn.execute("SELECT COUNT(*) c FROM users WHERE kick_id=?", (kid,)).fetchone()["c"]
        assert cnt == 1, "nesmí vzniknout druhý účet"
    finally:
        conn.close()


def test_prefers_non_banned_on_shared_kick_id(client):
    """Když dva účty sdílí kick_id (starý zabanovaný duplikát), login vrátí ten NEzabanovaný."""
    conn = get_conn()
    try:
        kid = "kid_" + secrets.token_hex(5)
        _mk(conn, "dupe_" + secrets.token_hex(3), kid, banned=1)
        live_id = _mk(conn, "live_" + secrets.token_hex(3), kid, banned=0)
        conn.commit()
        row = _find_or_create_kick_user(conn, "login_" + secrets.token_hex(3), "x", kick_id=kid)
        assert row["id"] == live_id and not row["banned"], "musí preferovat NEzabanovaný účet"
    finally:
        conn.close()


def test_new_user_created_when_no_match(client):
    """Žádná shoda (nový kick_id i nick) → založí se nový účet."""
    conn = get_conn()
    try:
        kid = "kid_" + secrets.token_hex(5)
        nick = "fresh_" + secrets.token_hex(3)
        row = _find_or_create_kick_user(conn, nick, nick, kick_id=kid)
        assert row["kick_username"] == nick and row["kick_id"] == kid
        assert row["points"] == 0
    finally:
        conn.close()


def test_legacy_ghost_matches_by_nick_and_gets_kick_id(client):
    """Starý/ghost účet bez kick_id se spáruje podle nicku a doplní se mu kick_id (zpětná kompat.)."""
    conn = get_conn()
    try:
        nick = "ghost_" + secrets.token_hex(3)
        gid = _mk(conn, nick, None, points=300)
        conn.commit()
        kid = "kid_" + secrets.token_hex(5)
        row = _find_or_create_kick_user(conn, nick, nick, kick_id=kid)
        assert row["id"] == gid and row["points"] == 300, "starý účet se převezme podle nicku"
        assert row["kick_id"] == kid, "doplní se mu kick_id pro příště"
    finally:
        conn.close()
