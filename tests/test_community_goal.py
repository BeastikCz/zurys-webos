"""Komunitní chat cíl: tick plní, naplnění vyplatí všem dnešním přispěvatelům
(jen jednou), reset na nový den.

    .venv/Scripts/python.exe -m pytest tests/test_community_goal.py -v
"""
import secrets

from app import community_goal, kickbot
from app.db import get_conn, now_iso, set_setting


def test_status_resets_on_new_day(client):
    conn = get_conn()
    try:
        set_setting(conn, "cgoal_day", "2000-01-01")
        set_setting(conn, "cgoal_progress", "55")
        set_setting(conn, "cgoal_done", "1")
        conn.commit()
        st = community_goal.status(conn)        # _ensure_day → reset na dnešek
        assert st["progress"] == 0 and st["done"] is False
    finally:
        conn.close()


def test_goal_fires_and_rewards_contributors(client, monkeypatch):
    monkeypatch.setattr(kickbot, "send_message", lambda *a, **k: None)
    conn = get_conn()
    try:
        set_setting(conn, "cgoal_enabled", "1")
        set_setting(conn, "cgoal_target", "2")
        set_setting(conn, "cgoal_reward", "777")
        set_setting(conn, "cgoal_day", community_goal._today())
        set_setting(conn, "cgoal_progress", "0")
        set_setting(conn, "cgoal_done", "0")
        uname = f"cg_{secrets.token_hex(4)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (uname, uname, "user", 0, now_iso())).lastrowid
        conn.execute(
            "INSERT INTO activity_state (user_id, day, earned_today, watch_today, chat_today, last_chat_at) "
            "VALUES (?,?,?,?,?,?)", (uid, community_goal._today(), 1, 0, 1, now_iso()))
        conn.commit()

        community_goal.tick(conn)               # 1/2
        community_goal.tick(conn)               # 2/2 → naplněno → výplata
        st = community_goal.status(conn)
        assert st["done"] is True
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 777

        community_goal.tick(conn)               # nesmí vyplatit znovu
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 777
    finally:
        conn.close()


def test_disabled_goal_does_not_tick(client):
    conn = get_conn()
    try:
        set_setting(conn, "cgoal_enabled", "0")
        set_setting(conn, "cgoal_day", community_goal._today())
        set_setting(conn, "cgoal_progress", "0")
        conn.commit()
        community_goal.tick(conn)
        assert community_goal._int(conn, "cgoal_progress", -1) == 0   # nic nepřibylo
    finally:
        set_setting(conn, "cgoal_enabled", "1"); conn.commit()
        conn.close()
