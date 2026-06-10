"""Top Chatteři: žebříček podle počtu zpráv + denní výplata TOP 3 (silent-init,
bez dvojí výplaty).

    .venv/Scripts/python.exe -m pytest tests/test_topchatter.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app import topchatter, kickbot
from app.db import get_conn, now_iso, get_setting, set_setting, local_date, local_day_start_iso


def _mk(conn, name):
    return conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
        (f"{name}_{secrets.token_hex(3)}", name, "user", 0, now_iso())).lastrowid


def test_top_chatters_order(client):
    conn = get_conn()
    try:
        a, b = _mk(conn, "tcA"), _mk(conn, "tcB")
        for _ in range(50):   # A hodně → bezpečně nahoře
            conn.execute("INSERT INTO points_log (user_id,change,reason,created_at) VALUES (?,1,'Aktivita v chatu',?)", (a, now_iso()))
        conn.execute("INSERT INTO points_log (user_id,change,reason,created_at) VALUES (?,1,'Aktivita v chatu',?)", (b, now_iso()))
        conn.commit()
        top = topchatter.top_chatters(conn, "day", 100)
        d = {t["username"]: t["msgs"] for t in top}
        assert d.get("tcA") == 50 and d.get("tcB") == 1
        names = [t["username"] for t in top]
        assert names.index("tcA") < names.index("tcB")
    finally:
        conn.close()


def test_bot_excluded_from_leaderboard(client):
    """Bot (jméno v BOT_USERNAMES) se nesmí objevit v Top Chatterech ani s tunou zpráv."""
    conn = get_conn()
    try:
        bid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            ("nightbot", "nightbot", "user", 0, now_iso())).lastrowid
        for _ in range(99):
            conn.execute("INSERT INTO points_log (user_id,change,reason,created_at) VALUES (?,1,'Aktivita v chatu',?)", (bid, now_iso()))
        conn.commit()
        names = [t["username"].lower() for t in topchatter.top_chatters(conn, "day", 100)]
        assert "nightbot" not in names
    finally:
        conn.close()


def test_payout_silent_init(client):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM app_settings WHERE key='topchat_paid_day'")
        conn.commit()
        assert topchatter.maybe_payout(conn) == 0          # silent init
        assert get_setting(conn, "topchat_paid_day") == local_date(-1)   # včerejšek (ČR)
    finally:
        conn.close()


def test_payout_rewards_yesterday_top(client, monkeypatch):
    monkeypatch.setattr(kickbot, "send_message", lambda *a, **k: None)
    conn = get_conn()
    try:
        uid = _mk(conn, "tcWin")
        # uprostřed VČEREJŠÍHO českého dne (v UTC), ať to padne do výplatního okna
        yday_mid = (datetime.fromisoformat(local_day_start_iso(-1)) + timedelta(hours=12)).isoformat()
        for _ in range(5):
            conn.execute("INSERT INTO points_log (user_id,change,reason,created_at) VALUES (?,1,'Aktivita v chatu',?)", (uid, yday_mid))
        # paid_day = předevčírem (ČR) → vyplatí za včerejšek
        set_setting(conn, "topchat_paid_day", local_date(-2))
        conn.commit()
        topchatter.maybe_payout(conn)
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 3000
        topchatter.maybe_payout(conn)          # podruhé už ne
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 3000
    finally:
        conn.close()
