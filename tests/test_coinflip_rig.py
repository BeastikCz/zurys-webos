"""Dočasný coinflip rig (config v app_settings, gate na hráče + dnešní den). Default vypnuto.

    .venv/Scripts/python.exe -m pytest tests/test_coinflip_rig.py -v
"""
import secrets


def _user(conn, name):
    from app.db import now_iso
    return conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
        (name.lower(), name, "user", now_iso())).lastrowid


def _set(conn, user, pct, date):
    from app.db import set_setting
    set_setting(conn, "coinflip_rig_user", user)
    set_setting(conn, "coinflip_rig_pct", str(pct))
    set_setting(conn, "coinflip_rig_date", date)
    conn.commit()


def _clear(conn):
    from app.db import set_setting
    for k in ("coinflip_rig_user", "coinflip_rig_pct", "coinflip_rig_date"):
        set_setting(conn, k, "")
    conn.commit()


def test_rig_biases_target_player():
    from app.db import get_conn, local_date
    from app.routers.games import _coinflip_rig_winner
    conn = get_conn()
    try:
        rig = _user(conn, f"rig_{secrets.token_hex(3)}")
        opp = _user(conn, f"opp_{secrets.token_hex(3)}")
        rname = conn.execute("SELECT username FROM users WHERE id=?", (rig,)).fetchone()["username"]
        _set(conn, rname, 80, local_date())
        d = {"p1_id": rig, "p2_id": opp}
        wins = sum(1 for _ in range(1000) if _coinflip_rig_winner(conn, d) == 1)
        assert 700 < wins < 900, f"~80% čekáno, p1(rig) vyhrál {wins}/1000"
        # rigovaný jako p2 → bias na 2
        d2 = {"p1_id": opp, "p2_id": rig}
        wins2 = sum(1 for _ in range(1000) if _coinflip_rig_winner(conn, d2) == 2)
        assert 700 < wins2 < 900, f"p2(rig) vyhrál {wins2}/1000"
    finally:
        _clear(conn)
        conn.close()


def test_rig_off_by_default():
    from app.db import get_conn
    from app.routers.games import _coinflip_rig_winner
    conn = get_conn()
    try:
        _clear(conn)
        a, b = _user(conn, f"a_{secrets.token_hex(3)}"), _user(conn, f"b_{secrets.token_hex(3)}")
        assert all(_coinflip_rig_winner(conn, {"p1_id": a, "p2_id": b}) is None for _ in range(50))
    finally:
        conn.close()


def test_rig_wrong_day_inactive():
    from app.db import get_conn
    from app.routers.games import _coinflip_rig_winner
    conn = get_conn()
    try:
        rig = _user(conn, f"rig_{secrets.token_hex(3)}")
        opp = _user(conn, f"opp_{secrets.token_hex(3)}")
        rname = conn.execute("SELECT username FROM users WHERE id=?", (rig,)).fetchone()["username"]
        _set(conn, rname, 80, "2000-01-01")           # dávno → neaktivní
        assert all(_coinflip_rig_winner(conn, {"p1_id": rig, "p2_id": opp}) is None for _ in range(50))
    finally:
        _clear(conn)
        conn.close()


def test_rig_user_not_in_duel():
    from app.db import get_conn, local_date
    from app.routers.games import _coinflip_rig_winner
    conn = get_conn()
    try:
        rig = _user(conn, f"rig_{secrets.token_hex(3)}")
        a, b = _user(conn, f"a_{secrets.token_hex(3)}"), _user(conn, f"b_{secrets.token_hex(3)}")
        rname = conn.execute("SELECT username FROM users WHERE id=?", (rig,)).fetchone()["username"]
        _set(conn, rname, 80, local_date())
        assert all(_coinflip_rig_winner(conn, {"p1_id": a, "p2_id": b}) is None for _ in range(50))
    finally:
        _clear(conn)
        conn.close()
