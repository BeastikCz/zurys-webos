"""Body za chat se připisují JEN když je stream live (offline chat se nepočítá).

    .venv/Scripts/python.exe -m pytest tests/test_chat_live.py -v
"""
import secrets

from app import economy
from app.db import get_conn, now_iso


def _mk(conn):
    uname = f"cl_{secrets.token_hex(4)}"
    conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
                 (uname, uname, "user", 0, now_iso()))
    return conn.execute("SELECT * FROM users WHERE kick_username=?", (uname,)).fetchone()


def test_chat_offline_not_awarded(client, monkeypatch):
    """Stream offline → award_chat nic nepřipíše (offline)."""
    monkeypatch.setattr("app.live.is_live", lambda conn: False)
    conn = get_conn()
    try:
        u = _mk(conn)
        conn.commit()
        res = economy.award_chat(conn, u)
        assert res.get("offline") is True and res["awarded"] == 0
    finally:
        conn.close()


def test_chat_online_awarded(client, monkeypatch):
    """Stream live → offline pojistka neblokuje (award_chat normálně projde)."""
    monkeypatch.setattr("app.live.is_live", lambda conn: True)
    monkeypatch.setattr("app.community_goal.tick", lambda conn: None)   # izolace od komunitního cíle
    conn = get_conn()
    try:
        u = _mk(conn)
        conn.commit()
        res = economy.award_chat(conn, u)
        assert res.get("offline") is None        # offline větev se netrigne
    finally:
        conn.close()
