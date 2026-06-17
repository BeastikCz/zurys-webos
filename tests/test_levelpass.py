"""Level Pass: milníky podle úrovně → grant exkluzivní kosmetiky. cosmetic_owns = ledger
(vlastníš odměnu = milník vyzvednut), takže žádná vlastní tabulka + claim je idempotentní.

    .venv/Scripts/python.exe -m pytest tests/test_levelpass.py -v
"""
import secrets


def _mk(conn, earned=0):
    from app.db import now_iso
    u = f"lp_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, earned_total, created_at) "
        "VALUES (?,?,?,?,?,?)", (u, u, "user", 1000, earned, now_iso())).lastrowid
    conn.commit()
    return uid


def _row(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def _set_earned(conn, uid, et):
    conn.execute("UPDATE users SET earned_total = ? WHERE id = ?", (et, uid))
    conn.commit()


def test_levelpass_locked_then_claim(client):
    from app.db import get_conn
    from app import levelpass
    conn = get_conn()
    try:
        uid = _mk(conn, earned=0)
        st = levelpass.status(conn, _row(conn, uid))
        assert st["level"] == 1
        assert all(not m["reached"] for m in st["milestones"])
        assert st["claimable"] == 0

        # nedosažený milník nejde vyzvednout (server ověří úroveň)
        assert levelpass.claim(conn, _row(conn, uid), 10)["ok"] is False

        # vylevluj na 10 (earned_total = 300 * 9^2 = 24300)
        _set_earned(conn, uid, 24300)
        st = levelpass.status(conn, _row(conn, uid))
        assert st["level"] == 10
        m10 = next(m for m in st["milestones"] if m["level"] == 10)
        assert m10["reached"] and not m10["claimed"]
        assert st["claimable"] == 1

        # claim lvl 10 → grant frame_pass10 do cosmetic_owns
        r = levelpass.claim(conn, _row(conn, uid), 10)
        assert r["ok"] and "frame_pass10" in r["granted"]
        assert conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id=? AND item_key='frame_pass10'",
                            (uid,)).fetchone()

        # idempotentní – podruhé fail
        assert levelpass.claim(conn, _row(conn, uid), 10)["ok"] is False
        st = levelpass.status(conn, _row(conn, uid))
        assert next(m for m in st["milestones"] if m["level"] == 10)["claimed"] is True
        # vyšší milník pořád zamčený
        assert levelpass.claim(conn, _row(conn, uid), 25)["ok"] is False
    finally:
        conn.close()


def test_levelpass_legend_grants_both(client):
    from app.db import get_conn
    from app import levelpass
    conn = get_conn()
    try:
        uid = _mk(conn, earned=2940300)   # lvl 100 = 300 * 99^2
        st = levelpass.status(conn, _row(conn, uid))
        assert st["level"] == 100
        m100 = next(m for m in st["milestones"] if m["level"] == 100)
        assert m100["irl"] is True and len(m100["rewards"]) == 2

        # lvl 100 = trofejový rámeček + Legenda nick, oboje grant
        r = levelpass.claim(conn, _row(conn, uid), 100)
        assert r["ok"] and r["irl"] is True
        assert "frame_legend" in r["granted"] and "name_legend" in r["granted"]
        for k in ("frame_legend", "name_legend"):
            assert conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id=? AND item_key=?",
                                (uid, k)).fetchone()
    finally:
        conn.close()
