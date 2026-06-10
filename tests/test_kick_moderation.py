"""Sync banu z webu do Kick chatu: guardraily moderate_ban/unban (bez sítě, skončí na
kontrolách demo/scope/ID) + ban endpoint vrací stav kick syncu (skipped bez kick_id).

    .venv/Scripts/python.exe -m pytest tests/test_kick_moderation.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app import kickbot
from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _login_as(role: str) -> str:
    conn = get_conn()
    try:
        suffix = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"{role}_{suffix}", f"{role}_{suffix}", role, now_iso()))
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, cur.lastrowid, now_iso(),
             (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def test_moderate_ban_demo_mode(client):
    conn = get_conn()
    try:
        kickbot.connect_demo(conn)
        conn.commit()
        r = kickbot.moderate_ban(conn, "12345", reason="test")
        assert r["ok"] is False and "demo" in r["error"].lower()
    finally:
        kickbot.disconnect(conn)
        conn.commit()
        conn.close()


def test_moderate_ban_requires_scope(client):
    """Starý token bez moderation:ban → srozumitelná chyba (znovu připojit bota)."""
    conn = get_conn()
    try:
        kickbot.save_real_token(conn, "bot", "tok", "ref", 3600,
                                "user:read chat:write events:subscribe", "kanal", "99")
        conn.commit()
        r = kickbot.moderate_ban(conn, "12345")
        assert r["ok"] is False and "moderation:ban" in r["error"]
        r2 = kickbot.moderate_unban(conn, "12345")
        assert r2["ok"] is False and "moderation:ban" in r2["error"]
    finally:
        kickbot.disconnect(conn)
        conn.commit()
        conn.close()


def test_moderate_ban_invalid_kick_id(client):
    conn = get_conn()
    try:
        kickbot.save_real_token(conn, "bot", "tok", "ref", 3600,
                                "chat:write moderation:ban", "kanal", "99")
        conn.commit()
        r = kickbot.moderate_ban(conn, "")          # ghost bez kick_id / nesmysl
        assert r["ok"] is False and "Kick ID" in r["error"]
    finally:
        kickbot.disconnect(conn)
        conn.commit()
        conn.close()


def test_ban_endpoint_kick_skipped_without_kick_id(client):
    """Ban i unban účtu bez kick_id: web část projde, kick sync se jen přeskočí."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"g_{secrets.token_hex(4)}", f"g_{secrets.token_hex(4)}", "user", now_iso()))
        conn.commit()
        uid = cur.lastrowid
    finally:
        conn.close()
    tok = _login_as("admin")
    r = client.post(f"/api/admin/users/{uid}/ban", json={"banned": True, "reason": "t"},
                    headers={"Cookie": f"{SESSION_COOKIE}={tok}"})
    assert r.status_code == 200
    j = r.json()
    assert j["banned"] is True and j["kick"]["skipped"] is True
    r2 = client.post(f"/api/admin/users/{uid}/ban", json={"banned": False, "reason": ""},
                     headers={"Cookie": f"{SESSION_COOKIE}={tok}"})
    assert r2.status_code == 200 and r2.json()["kick"]["skipped"] is True
