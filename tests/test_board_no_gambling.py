"""Weekly + season board (_earned_board) sčítají JEN legit vydělané sedláky –
gambling (Mines/duely/predikce), admin granty (botrix) a transfery se NEpočítají.
Hranice = classify_xp(reason)[0] != 'zero' (stejná jako XP/levely).

    .venv/Scripts/python.exe -m pytest tests/test_board_no_gambling.py -v
"""
import secrets


def _mk_user(conn):
    from app.db import now_iso
    u = f"bg_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
        (u, u, "user", now_iso())).lastrowid
    return u, uid


def _log(conn, uid, change, reason):
    from app.db import now_iso
    conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                 (uid, change, reason, now_iso()))


# legit (počítá se do boardu) + jeho přesný součet
_LEGIT = [
    (1000, "Sledování streamu"),
    (500, "Aktivita v chatu"),
    (1400, "Sklizeň: Zlatý klas 🌾"),
    (100, "Úkol: Zahradník 📋"),
    (5000, "Kick gift sub 🎁 ×1"),
]
_LEGIT_SUM = sum(c for c, _ in _LEGIT)   # 8000

# zero (NESMÍ se počítat) – gambling, admin grant, transfery, refundy
_ZERO = [
    (999999, "Mines cashout (×8.16)"),
    (20000, "Coinflip duel #13901 – výhra"),
    (5000, "Predikce #3 – výhra"),
    (10000, "Vypršelá výzva (duel #13231) – vrácení vkladu"),
    (9000, "botrix"),
    (2000, "Dar od Kubax1kCZ 🎁"),
    (1000, "Sub cíl komunity 🟣🎁"),
]


def _seed(conn):
    u, uid = _mk_user(conn)
    for ch, r in _LEGIT + _ZERO:
        _log(conn, uid, ch, r)
    conn.commit()
    return u


def _gained(rows, username):
    for r in rows:
        if r["username"] == username:
            return r["gained"]
    return None


def test_weekly_excludes_gambling(client):
    from app.db import get_conn
    from app.routers import misc
    conn = get_conn()
    try:
        u = _seed(conn)
    finally:
        conn.close()
    misc._weekly_cache["data"] = None
    d = client.get("/api/leaderboard/weekly").json()
    g = _gained(d["rows"], u)
    assert g == _LEGIT_SUM, f"weekly má být jen legit {_LEGIT_SUM}, ne {g} (gambling/admin/transfer prosákl)"


def test_season_excludes_gambling(client):
    from app.db import get_conn
    from app.routers import misc
    conn = get_conn()
    try:
        u = _seed(conn)
    finally:
        conn.close()
    misc._season_cache["data"] = None
    d = client.get("/api/leaderboard/season").json()
    g = _gained(d["rows"], u)
    assert g == _LEGIT_SUM, f"season má být jen legit {_LEGIT_SUM}, ne {g}"


def test_pure_gambler_absent(client):
    """Hráč co má JEN gambling (žádné legit) se na boardu vůbec neobjeví (gained by bylo 0)."""
    from app.db import get_conn
    from app.routers import misc
    conn = get_conn()
    try:
        u, uid = _mk_user(conn)
        for ch, r in _ZERO:
            _log(conn, uid, ch, r)
        conn.commit()
    finally:
        conn.close()
    misc._weekly_cache["data"] = None
    d = client.get("/api/leaderboard/weekly").json()
    assert _gained(d["rows"], u) is None, "čistý gambler nemá být na boardu"
