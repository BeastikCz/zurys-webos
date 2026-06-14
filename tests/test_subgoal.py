"""Komunitní SUB cíl: tick plní cíl, po naplnění odměna všem dnešním aktivním divákům (1×).

    .venv/Scripts/python.exe -m pytest tests/test_subgoal.py -v
"""
import secrets


def _user_active_today(conn):
    from app.db import now_iso, local_date
    u = f"sg_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
        (u, u, "user", 0, now_iso())).lastrowid
    conn.execute("INSERT INTO activity_state (user_id, day, watch_today, chat_today) VALUES (?,?,?,?)",
                 (uid, local_date(), 1, 0))
    conn.commit()
    return uid


def test_subgoal_fires_and_rewards_active_once(client):
    from app.db import get_conn, set_setting
    from app import subgoal
    conn = get_conn()
    try:
        set_setting(conn, "subgoal_enabled", "1")
        set_setting(conn, "subgoal_target", "3")
        set_setting(conn, "subgoal_reward", "100")
        set_setting(conn, "subgoal_day", subgoal._today())   # dnešní den → žádný reset uprostřed testu
        set_setting(conn, "subgoal_progress", "0")
        set_setting(conn, "subgoal_done", "0")
        uid = _user_active_today(conn)

        subgoal.tick(conn, 1)
        subgoal.tick(conn, 1)
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 0, \
            "pod cílem se nesmí nic vyplatit"

        subgoal.tick(conn, 1)                                # 3. = dosažení cíle → výplata
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 100
        assert subgoal.status(conn)["done"] is True

        subgoal.tick(conn, 5)                                # další subby už nevyplácí (1×/den)
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 100, \
            "výplata jen jednou za den"
    finally:
        conn.close()


def test_subgoal_disabled_does_nothing(client):
    from app.db import get_conn, set_setting
    from app import subgoal
    conn = get_conn()
    try:
        set_setting(conn, "subgoal_enabled", "0")
        set_setting(conn, "subgoal_day", subgoal._today())
        set_setting(conn, "subgoal_progress", "0")
        set_setting(conn, "subgoal_done", "0")
        subgoal.tick(conn, 100)
        assert subgoal.status(conn)["progress"] == 0, "vypnutý cíl se neplní"
    finally:
        conn.close()
