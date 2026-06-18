"""Nový XP model (supporter-first) → earned_total (lifetime XP → level / Battle Pass):
 • supporter (vlastní sub / resub / gift sub giver) = PEVNÝCH 5000 XP za KAŽDÝ sub (z počtu v reason,
   ne z bodů → HH 2× bonus XP nezdvojí), BEZ stropu = náskok podporovatelů
 • poctivé farmení = body × faktor (kolo/drop/partner 0.5, zbytek 1.0), DENNÍ strop XP (sub ×1.5 + vyšší strop)
 • gambling / dary / admin / komunitní cíle / botrix = 0
 • import staré platformy = plně, bez stropu
Zůstatek (points) se mění vždy plně.

    .venv/Scripts/python.exe -m pytest tests/test_earned_gambling.py -v
"""
import secrets


def _mk(conn, is_sub=0):
    from app.db import now_iso
    u = f"et_{secrets.token_hex(3)}"
    return conn.execute(
        "INSERT INTO users (kick_username, username, role, points, earned_total, is_sub, created_at) "
        "VALUES (?,?,?,0,0,?,?)", (u, u, "user", is_sub, now_iso())).lastrowid


def _et(conn, uid):
    return conn.execute("SELECT earned_total FROM users WHERE id=?", (uid,)).fetchone()["earned_total"]


def test_classify_xp():
    from app.deps import classify_xp
    # supporter (počet subů z reason)
    assert classify_xp("Kick gift sub 🎁 ×5") == ("sup", 5)
    assert classify_xp("Kick gift sub 🎁 ×2 (happy 2×)") == ("sup", 2)   # HH nezdvojí počet
    assert classify_xp("Kick sub 🟣") == ("sup", 1)
    assert classify_xp("Kick resub 🔁") == ("sup", 1)
    assert classify_xp("Kick gift sub (příjemce)")[0] != "sup"          # příjemce nebere
    # import
    assert classify_xp("Import ze staré platformy")[0] == "imp"
    # nulové buckety
    for r in ["Mines cashout (×2)", "Coinflip duel", "Predikce #3 – výhra", "Vrácení vkladu – hra #5",
              "Úprava adminem", "Sub cíl komunity 🟣🎁", "Sub cíl tier 3 🟣🎁", "Komunitní chat cíl 🎉",
              "botrix", "Dar od X 🎁", "Battle Pass tier 5 🎟️"]:
        assert classify_xp(r)[0] == "zero", r
    # farmení (faktor)
    assert classify_xp("Sledování streamu") == ("farm", 1.0)
    assert classify_xp("Aktivita v chatu") == ("farm", 1.0)
    assert classify_xp("Sklizeň: Mrkev 🌾") == ("farm", 1.0)
    assert classify_xp("Kolo štěstí 🎡") == ("farm", 0.5)
    assert classify_xp("Drop #5 – 1. místo") == ("farm", 0.5)
    assert classify_xp("Flash partner: XY") == ("farm", 0.5)


def test_supporter_xp_flat_per_sub(client):
    from app.db import get_conn
    from app.deps import add_points, XP_PER_SUB
    conn = get_conn()
    try:
        uid = _mk(conn)
        add_points(conn, uid, 5000, "Kick gift sub 🎁 ×5")              # 5 subů → 5×5000 (body ignorovány)
        conn.commit()
        assert _et(conn, uid) == 5 * XP_PER_SUB
        add_points(conn, uid, 4000, "Kick gift sub 🎁 ×2 (happy 2×)")   # HH 2× body NEzdvojí XP → 2 subů
        conn.commit()
        assert _et(conn, uid) == (5 + 2) * XP_PER_SUB
    finally:
        conn.close()


def test_zero_buckets_no_xp(client):
    from app.db import get_conn
    from app.deps import add_points
    conn = get_conn()
    try:
        uid = _mk(conn)
        for ch, r in [(5000, "Mines cashout (×2)"), (2000, "Coinflip duel"), (1000, "Úprava adminem"),
                      (1000, "Sub cíl komunity 🟣🎁"), (1000, "Komunitní chat cíl 🎉"), (500, "botrix"),
                      (300, "Vrácení vkladu – hra #5")]:
            add_points(conn, uid, ch, r)
        conn.commit()
        assert _et(conn, uid) == 0
    finally:
        conn.close()


def test_farm_daily_cap(client):
    from app.db import get_conn
    from app.deps import add_points, FARM_XP_CAP
    conn = get_conn()
    try:
        uid = _mk(conn)                                    # non-sub → strop 2000
        add_points(conn, uid, 5000, "Sledování streamu")   # farm 1.0, ale denní strop 2000
        conn.commit()
        assert _et(conn, uid) == FARM_XP_CAP               # zbytek nad strop propadá
    finally:
        conn.close()


def test_kolo_half_factor(client):
    from app.db import get_conn
    from app.deps import add_points
    conn = get_conn()
    try:
        uid = _mk(conn)
        add_points(conn, uid, 800, "Kolo štěstí 🎡")       # 0.5 faktor → 400
        conn.commit()
        assert _et(conn, uid) == 400
    finally:
        conn.close()


def test_sub_farm_multiplier_and_higher_cap(client):
    from app.db import get_conn
    from app.deps import add_points, FARM_XP_CAP_SUB
    conn = get_conn()
    try:
        uid = _mk(conn, is_sub=1)                          # sub → ×1.5 + strop 3000
        add_points(conn, uid, 1000, "Sledování streamu")   # 1000 × 1.5 = 1500
        conn.commit()
        assert _et(conn, uid) == 1500
        add_points(conn, uid, 5000, "Sledování streamu")   # 5000×1.5=7500, ale strop 3000 (už 1500) → +1500
        conn.commit()
        assert _et(conn, uid) == FARM_XP_CAP_SUB
    finally:
        conn.close()


def test_import_full_uncapped(client):
    from app.db import get_conn
    from app.deps import add_points
    conn = get_conn()
    try:
        uid = _mk(conn)
        add_points(conn, uid, 50000, "Import ze staré platformy")   # plně, bez stropu
        conn.commit()
        assert _et(conn, uid) == 50000
    finally:
        conn.close()


def test_admin_grant_xp_false_no_xp(client):
    from app.db import get_conn
    from app.deps import add_points
    conn = get_conn()
    try:
        uid = _mk(conn)
        add_points(conn, uid, 9999, "Bonus", xp=False)     # xp=False → 0 XP bez ohledu na reason
        conn.commit()
        assert _et(conn, uid) == 0
        assert conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"] == 9999
    finally:
        conn.close()
