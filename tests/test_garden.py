"""Zahrádka: zasaď (zaplať sazbu) → po dorostení sklidíš (odměna).

    .venv/Scripts/python.exe -m pytest tests/test_garden.py -v
"""
import secrets


def _mk(conn, points=500):
    from app.db import now_iso
    u = f"g_{secrets.token_hex(3)}"
    uid = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
                       (u, u, "user", points, now_iso())).lastrowid
    conn.commit()
    return uid


def test_garden_plant_grow_harvest(client):
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=500)
        user = {"id": uid}
        st = garden.status(conn, user)
        assert len(st["plots"]) == garden.N_PLOTS and st["plots"][0]["empty"]

        r = garden.plant(conn, user, 0, "mrkev")        # sazba 50
        assert r["ok"] and r["balance"] == 450
        assert garden.plant(conn, user, 0, "mrkev")["ok"] is False     # obsazený
        assert garden.harvest(conn, user, 0)["ok"] is False            # nedorostlo

        # nasimuluj dorostení
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00' WHERE user_id=? AND plot=0", (uid,))
        conn.commit()
        h = garden.harvest(conn, user, 0)
        assert h["ok"] and h["reward"] == 80 and h["balance"] == 450 + 80
        assert garden.status(conn, user)["plots"][0]["empty"]          # zase volný

        # málo sedláků na klas (1000) → fail
        conn.execute("UPDATE users SET points=10 WHERE id=?", (uid,)); conn.commit()
        assert garden.plant(conn, user, 1, "klas")["ok"] is False
        assert garden.plant(conn, user, 1, "neznama")["ok"] is False   # neznámá plodina
    finally:
        conn.close()
