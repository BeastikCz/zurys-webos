"""Herna jen pro admina: env zámek na backendu (WEBOS_GAMES_ADMIN_ONLY) + flag v /api/auth/me.

    .venv/Scripts/python.exe -m pytest tests/test_games_admin_only.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _mk_session(role: str = "user") -> str:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"{role}_{suf}", f"{role}_{suf}", role, 5000, now_iso()))
        tok = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (tok, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return tok
    finally:
        conn.close()


def _hdr(tok):
    return {"Cookie": f"{SESSION_COOKIE}={tok}"}


def test_auth_me_exposes_flag(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert "games_admin_only" in r.json()


def test_games_open_for_all_when_flag_off(client):
    # default (env off v testech) → Herna pro všechny, divák NEdostane 403
    r = client.get("/api/games/open", headers=_hdr(_mk_session("user")))
    assert r.status_code != 403, r.text


def test_games_blocked_for_non_admin_when_flag_on(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "GAMES_ADMIN_ONLY", True)
    # divák → 403 na herní endpoint
    assert client.get("/api/games/open", headers=_hdr(_mk_session("user"))).status_code == 403
    # admin → projde (ne 403)
    assert client.get("/api/games/open", headers=_hdr(_mk_session("admin"))).status_code != 403


def test_blackjack_blocked_for_non_admin_when_flag_on(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "GAMES_ADMIN_ONLY", True)
    # blackjack router musí být zamčený taky (Herna = duely + blackjack)
    assert client.get("/api/blackjack/room/mine", headers=_hdr(_mk_session("user"))).status_code == 403
    assert client.get("/api/blackjack/room/mine", headers=_hdr(_mk_session("admin"))).status_code != 403
