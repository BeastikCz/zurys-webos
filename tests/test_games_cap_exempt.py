"""eco_games_exempt_uids: per-user výjimka z denního stropu zisku z her (jako eco_wager_exempt_uids).

    .venv/Scripts/python.exe -m pytest tests/test_games_cap_exempt.py -v
"""
import secrets


def test_games_cap_exempt():
    from app.db import get_conn, set_setting, now_iso
    from app.economy import games_capped, note_game_net
    conn = get_conn()
    try:
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"gc_{secrets.token_hex(3)}", "gc", "user", now_iso())).lastrowid
        set_setting(conn, "eco_games_cap", "15000")
        set_setting(conn, "eco_games_exempt_uids", "")
        note_game_net(conn, uid, 20000)               # net zisk 20000 dnes → nad strop 15000
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        assert games_capped(conn, user) is True, "nad strop bez exempt → capped"
        set_setting(conn, "eco_games_exempt_uids", f"[{uid}]")
        conn.commit()
        assert games_capped(conn, user) is False, "exempt → necapped i nad strop"
        set_setting(conn, "eco_games_exempt_uids", "[999999]")   # někdo jiný exempt → tenhle pořád capped
        conn.commit()
        assert games_capped(conn, user) is True, "cizí exempt nepomáhá"
    finally:
        set_setting(conn, "eco_games_exempt_uids", "")
        conn.commit()
        conn.close()


def test_cap_zero_never_caps():
    from app.db import get_conn, set_setting, now_iso
    from app.economy import games_capped, note_game_net
    conn = get_conn()
    try:
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"gz_{secrets.token_hex(3)}", "gz", "user", now_iso())).lastrowid
        set_setting(conn, "eco_games_cap", "0")        # 0 = bez stropu pro všechny
        note_game_net(conn, uid, 999999)
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        assert games_capped(conn, user) is False
    finally:
        conn.close()
