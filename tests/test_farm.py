"""Statek (mini-farma) MVP: koupit → nakrmit → produkce → sebrat (XP+sedláci) → hlad. Sloty base+sub.

    .venv/Scripts/python.exe -m pytest tests/test_farm.py -v
"""
import secrets


def _user(conn, is_sub=0):
    from app.db import now_iso
    u = f"farm_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, earned_total, is_sub, created_at) "
        "VALUES (?,?,?,?,0,?,?)", (u, u, "user", 50000, is_sub, now_iso())).lastrowid
    return uid


def _row(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def test_farm_full_loop():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        user = _row(conn, uid)
        # koupě
        r = farm.buy(conn, user, "chicken")
        assert r["ok"], r
        st = farm.status(conn, user)
        assert st["n_slots"] == 2 and st["slots"][0]["state"] == "hungry", st["slots"][0]
        # krmení → roste
        bal0 = _row(conn, uid)["points"]
        rf = farm.feed(conn, _row(conn, uid), 0)
        assert rf["ok"] and _row(conn, uid)["points"] == bal0 - 80, rf
        assert farm.status(conn, user)["slots"][0]["state"] == "growing"
        # sebrání moc brzy → chyba
        assert not farm.collect(conn, _row(conn, uid), 0).get("ok")
        # přetoč čas → ready_at do minulosti
        conn.execute("UPDATE farm_animals SET ready_at='2000-01-01T00:00:00+00:00' WHERE user_id=? AND slot=0", (uid,))
        conn.commit()
        et0 = _row(conn, uid)["earned_total"]
        bal1 = _row(conn, uid)["points"]
        rc = farm.collect(conn, _row(conn, uid), 0)
        assert rc["ok"] and rc["reward"] >= 130, rc
        assert _row(conn, uid)["points"] >= bal1 + 130, "produkt přičten"
        assert _row(conn, uid)["earned_total"] > et0, "produkt dal XP (garden bucket)"
        # po sebrání zase hlad
        assert farm.status(conn, user)["slots"][0]["state"] == "hungry"
        # druhé sebrání → už nic (anti-double)
        assert not farm.collect(conn, _row(conn, uid), 0).get("ok")
    finally:
        conn.close()


def test_slots_base_two_nonsub():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn, is_sub=0); conn.commit()
        assert farm.buy(conn, _row(conn, uid), "chicken")["ok"]
        assert farm.buy(conn, _row(conn, uid), "chicken")["ok"]
        third = farm.buy(conn, _row(conn, uid), "chicken")
        assert not third["ok"], "non-sub má jen 2 sloty"
    finally:
        conn.close()


def test_sub_extra_slot():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn, is_sub=1); conn.commit()
        assert farm.status(conn, _row(conn, uid))["n_slots"] == 3, "sub má 3 sloty"
        for _ in range(3):
            assert farm.buy(conn, _row(conn, uid), "chicken")["ok"]
        assert not farm.buy(conn, _row(conn, uid), "chicken")["ok"]
    finally:
        conn.close()


def test_feed_hungry_only():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        farm.buy(conn, _row(conn, uid), "chicken")
        assert farm.feed(conn, _row(conn, uid), 0)["ok"]
        # už produkuje → druhé krmení zamítnuto
        assert not farm.feed(conn, _row(conn, uid), 0).get("ok")
    finally:
        conn.close()
