"""Hnojivo: zbývající čas růstu ×0.5, 1× na výsadbu — kupuje čas, ne výnos.

    .venv/Scripts/python.exe -m pytest tests/test_garden_fert.py -v
"""
import datetime as dt
import secrets


def _mk(conn, points=500):
    from app.db import now_iso
    u = f"g_{secrets.token_hex(3)}"
    uid = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
                       (u, u, "user", points, now_iso())).lastrowid
    conn.commit()
    return uid


def test_fertilize_halves_remaining_and_is_one_shot(client):
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=1000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "dyne")            # 12 h, výnos 600, semínko 450
        bal0 = conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]

        r = garden.fertilize(conn, user, 0)
        assert r["ok"] and r["cost"] == 120            # 20 % z 600
        assert r["balance"] == bal0 - 120
        assert 5.9 * 3600 < r["seconds_left"] <= 6 * 3600 + 5   # ~6 h (půlka z 12)

        st = garden.status(conn, user)["plots"][0]
        assert st["fert"] is True

        assert garden.fertilize(conn, user, 0)["ok"] is False   # 1× na výsadbu
        assert garden.fertilize(conn, user, 1)["ok"] is False   # prázdný záhon
    finally:
        conn.close()


def test_fertilize_blocked_on_ready_and_poor(client):
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=1000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "mrkev")
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00' WHERE user_id=? AND plot=0", (uid,))
        conn.commit()
        assert garden.fertilize(conn, user, 0)["ok"] is False   # už dorostlá

        garden.plant(conn, user, 1, "dyne")
        conn.execute("UPDATE users SET points=5 WHERE id=?", (uid,)); conn.commit()
        assert garden.fertilize(conn, user, 1)["ok"] is False   # nemá na hnojivo
        # flag se po neúspěšném debitu vrací → jde pohnojit později
        assert conn.execute("SELECT fert FROM garden WHERE user_id=? AND plot=1", (uid,)).fetchone()["fert"] == 0
    finally:
        conn.close()


def test_fertilize_shifts_future_pest_proportionally(client):
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=2000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "dyne")
        now = dt.datetime.now(dt.timezone.utc)
        pest_at = (now + dt.timedelta(hours=8)).isoformat()
        conn.execute("UPDATE garden SET pest_at=? WHERE user_id=? AND plot=0", (pest_at, uid))
        conn.commit()
        assert garden.fertilize(conn, user, 0)["ok"]
        row = conn.execute("SELECT pest_at FROM garden WHERE user_id=? AND plot=0", (uid,)).fetchone()
        left = (dt.datetime.fromisoformat(row["pest_at"]) - now).total_seconds()
        assert 3.9 * 3600 < left <= 4 * 3600 + 5    # ~4 h (půlka z 8) — chrobáky hnojivo neobchází
    finally:
        conn.close()
