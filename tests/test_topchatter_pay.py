"""Ruční výplata TOP chatterů dne (po streamu): TOP 3 berou 3000/2000/1000, 1× denně.

    .venv/Scripts/python.exe -m pytest tests/test_topchatter_pay.py -v
"""
import secrets

from app.db import get_conn, now_iso, set_setting
from app import topchatter


def _mkuser() -> int:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"u_{suf}", f"u_{suf}", "user", now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _bal(uid: int) -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()


def _chat(conn, uid, n):
    for _ in range(n):
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,1,'Aktivita v chatu',?)",
                     (uid, now_iso()))


def test_pay_today_rewards_top3_and_idempotent(client):
    a, b, c = _mkuser(), _mkuser(), _mkuser()
    conn = get_conn()
    try:
        set_setting(conn, "topchat_paid_day", "2000-01-01")   # ať je dnešek 'nezaplacený'
        _chat(conn, a, 300); _chat(conn, b, 200); _chat(conn, c, 100)   # jasná dominance nad ostatními testy
        conn.commit()
        res = topchatter.pay_today(conn)
    finally:
        conn.close()
    assert res["ok"] and res["count"] == 3, res
    assert _bal(a) == 3000, "1. místo bere 3000"
    assert _bal(b) == 2000, "2. místo bere 2000"
    assert _bal(c) == 1000, "3. místo bere 1000"

    # druhé volání týž den → už zaplaceno (žádné dvojplacení)
    conn = get_conn()
    try:
        res2 = topchatter.pay_today(conn)
    finally:
        conn.close()
    assert not res2["ok"], "dvojí výplata téhož dne musí být odmítnuta"
    assert _bal(a) == 3000, "žádné druhé připsání"


def test_status_shows_today_top3(client):
    conn = get_conn()
    try:
        set_setting(conn, "topchat_paid_day", "2000-01-02")
        conn.commit()
        st = topchatter.status(conn)
    finally:
        conn.close()
    assert st["payout"] == [3000, 2000, 1000]
    assert "today_top3" in st and "already_paid_today" in st
