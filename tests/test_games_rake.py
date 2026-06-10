"""Rake (house edge) na hrách/duelech: admin nastaví %, projeví se ve čtení i v setting.
Na konci resetuje na 0, ať neovlivní payout testy duelů/her.

    .venv/Scripts/python.exe -m pytest tests/test_games_rake.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso, get_setting, set_setting


def _login_as(role):
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


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def _reset_rake():
    conn = get_conn()
    try:
        set_setting(conn, "games_rake_pct", "0")
        conn.commit()
    finally:
        conn.close()


def test_admin_sets_rake(client):
    tok = _login_as("admin")
    try:
        r = client.post("/api/admin/economy/games-rake", json={"rake_pct": 2}, headers=_hdr(tok))
        assert r.status_code == 200, r.text
        assert r.json()["rake_pct"] == 2
        got = client.get("/api/admin/economy/games-rake", headers=_hdr(tok)).json()
        assert got["rake_pct"] == 2
        conn = get_conn()
        try:
            assert get_setting(conn, "games_rake_pct") == "2"
        finally:
            conn.close()
    finally:
        _reset_rake()


def test_rake_over_limit_rejected(client):
    tok = _login_as("admin")
    r = client.post("/api/admin/economy/games-rake", json={"rake_pct": 99}, headers=_hdr(tok))
    assert r.status_code == 422, "rake > 50 musí padnout na validaci"


def test_mod_cannot_set_rake(client):
    tok = _login_as("mod")
    r = client.post("/api/admin/economy/games-rake", json={"rake_pct": 5}, headers=_hdr(tok))
    assert r.status_code == 403, f"mod nesmí měnit rake, dostal {r.status_code}"
    _reset_rake()
