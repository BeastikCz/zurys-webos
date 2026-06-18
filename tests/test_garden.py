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

        r = garden.plant(conn, user, 0, "mrkev")        # semínko 38 (75 % z výnosu 50)
        assert r["ok"] and r["balance"] == 462
        assert garden.plant(conn, user, 0, "mrkev")["ok"] is False     # obsazený
        assert garden.harvest(conn, user, 0)["ok"] is False            # nedorostlo

        # nasimuluj dorostení (pest=0 → deterministicky bez škůdců, plná sklizeň)
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0 WHERE user_id=? AND plot=0", (uid,))
        conn.commit()
        h = garden.harvest(conn, user, 0)
        assert h["ok"] and h["reward"] == 50 and h["balance"] == 462 + 50
        assert garden.status(conn, user)["plots"][0]["empty"]          # zase volný

        # málo sedláků na klas (semínko 1050 = 75 % z 1400) → fail
        conn.execute("UPDATE users SET points=10 WHERE id=?", (uid,)); conn.commit()
        assert garden.plant(conn, user, 1, "klas")["ok"] is False
        assert garden.plant(conn, user, 1, "neznama")["ok"] is False   # neznámá plodina
    finally:
        conn.close()


def test_garden_pest_rescue_and_penalty(client):
    """Škůdci: zachráníš (zaplať 25 % výnosu) → plná sklizeň; neošetříš → jen půlka."""
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=3000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "klas")
        garden.plant(conn, user, 1, "klas")
        # vynuť škůdce + dorostlé na obou
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=1 WHERE user_id=?", (uid,))
        conn.commit()
        p0 = garden.status(conn, user)["plots"][0]
        assert p0["pest"] is True and p0["rescue_cost"] == 280   # 20 % z 1400

        # plot 0: zachraň → plná sklizeň 1400
        rr = garden.rescue(conn, user, 0)
        assert rr["ok"] and rr["cost"] == 280
        h0 = garden.harvest(conn, user, 0)
        assert h0["ok"] and h0["pest"] is False and h0["reward"] == 1400

        # plot 1: bez záchrany → půlka (700)
        h1 = garden.harvest(conn, user, 1)
        assert h1["ok"] and h1["pest"] is True and h1["reward"] == 700

        # rescue na záhonu bez škůdců = fail
        garden.plant(conn, user, 2, "mrkev")
        conn.execute("UPDATE garden SET pest=0 WHERE user_id=? AND plot=2", (uid,)); conn.commit()
        assert garden.rescue(conn, user, 2)["ok"] is False
    finally:
        conn.close()


def test_garden_decor_buy(client):
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=3000)
        user = {"id": uid}
        st = garden.decor_status(conn, user)
        assert len(st["items"]) == len(garden.DECOR) and not st["owned_icons"]

        r = garden.buy_decor(conn, user, "sunflower")   # cost 500
        assert r["ok"] and r["balance"] == 2500
        assert garden.buy_decor(conn, user, "sunflower")["ok"] is False     # už vlastní
        assert "🌻" in garden.decor_status(conn, user)["owned_icons"]

        assert garden.buy_decor(conn, user, "rainbow")["ok"] is False       # 9000 > 2500
        assert garden.buy_decor(conn, user, "neznama")["ok"] is False       # neznámá
    finally:
        conn.close()
