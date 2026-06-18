"""Farmářský Battle Pass: tier z earned_total diffu, claim odemčeného tieru.

    .venv/Scripts/python.exe -m pytest tests/test_battlepass.py -v
"""
import secrets


def _mk(conn, earned=0):
    from app.db import now_iso
    u = f"bp_{secrets.token_hex(3)}"
    return conn.execute(
        "INSERT INTO users (kick_username, username, role, points, earned_total, created_at) "
        "VALUES (?,?,?,?,?,?)", (u, u, "user", 0, earned, now_iso())).lastrowid


def test_battlepass_progress_and_claim(client):
    from app.db import get_conn
    from app import battlepass
    conn = get_conn()
    try:
        uid = _mk(conn, earned=0)
        conn.commit()
        # první status nastaví baseline = 0
        st = battlepass.status(conn, {"id": uid})
        assert st["tier"] == 0 and st["claimable"] == 0 and len(st["tiers"]) == battlepass.N_TIERS

        # nafarmi 6000 XP → 2 tiery (TIER_XP 2500)
        conn.execute("UPDATE users SET earned_total = 6000 WHERE id=?", (uid,))
        conn.commit()
        st = battlepass.status(conn, {"id": uid})
        assert st["tier"] == 2 and st["claimable"] == 2

        # claim tier 1 → odměna
        r = battlepass.claim(conn, {"id": uid}, 1)
        assert r["ok"] and r["reward"] == battlepass.tier_reward(1)
        assert conn.execute("SELECT earned_total FROM users WHERE id=?", (uid,)).fetchone()["earned_total"] == 6000

        # claim stejného znovu → fail; claim neodemčeného (tier 5) → fail
        assert battlepass.claim(conn, {"id": uid}, 1)["ok"] is False
        assert battlepass.claim(conn, {"id": uid}, 5)["ok"] is False

        # milník tier 5 dává víc než běžný
        assert battlepass.tier_reward(5) > battlepass.tier_reward(4)
    finally:
        conn.close()


def test_battlepass_late_first_open_counts_current_season_xp(client):
    from app.db import get_conn
    from app.deps import add_points, XP_PER_SUB
    from app import battlepass
    conn = get_conn()
    try:
        uid = _mk(conn, earned=0)
        conn.commit()

        # XP nasbírané PŘED prvním otevřením passu se musí započítat – baseline = stav na ZAČÁTKU
        # sezóny (dopočteno), ne earned_total při otevření, jinak by pass vypadal navždy zamčený.
        # Supporter event = uncapped, 1 sub × 5000 = 5000 XP → 2 tiery (TIER_XP 2500).
        add_points(conn, uid, 1000, "Kick gift sub 🎁 ×1")
        conn.commit()

        st = battlepass.status(conn, {"id": uid})
        assert st["tier"] == 2
        assert st["claimable"] == 2
        assert st["xp"] == XP_PER_SUB                       # 1 sub = 5000 XP
    finally:
        conn.close()


def test_battlepass_api(client):
    from app.db import get_conn, now_iso
    from app.config import SESSION_COOKIE
    from datetime import datetime, timezone, timedelta
    conn = get_conn()
    try:
        from app import battlepass
        uid = _mk(conn, earned=8000)
        # baseline 0 pro aktuální sezónu (jako by sezóna začala když měl 0) → 8000 XP = 3 tiery
        conn.execute("INSERT INTO battlepass (user_id, season, baseline, claimed, created_at) VALUES (?,?,0,'[]',?)",
                     (uid, battlepass._season(), now_iso()))
        t = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (t, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
    finally:
        conn.close()
    h = {"Cookie": f"{SESSION_COOKIE}={t}"}
    st = client.get("/api/battlepass", headers=h).json()
    assert st["tier"] == 3
    r = client.post("/api/battlepass/claim", json={"tier": 2}, headers=h)
    assert r.status_code == 200 and r.json()["ok"]
    # neodemčený tier → 400
    assert client.post("/api/battlepass/claim", json={"tier": 9}, headers=h).status_code == 400


def test_battlepass_premium(client):
    """Prémiová (sub-only) řada: 3× odměna, jen pro suby, nezávislá na free řadě."""
    from app.db import get_conn
    from app import battlepass
    conn = get_conn()
    try:
        uid = _mk(conn, earned=0)
        conn.commit()
        sub = {"id": uid, "is_sub": 1, "role": "user"}
        non = {"id": uid, "is_sub": 0, "role": "user"}
        st = battlepass.status(conn, sub)                 # baseline = 0 (earned=0)
        conn.execute("UPDATE users SET earned_total = 6000 WHERE id=?", (uid,))   # → 2 tiery odemčené
        conn.commit()
        st = battlepass.status(conn, sub)
        assert st["is_premium"] is True
        assert st["tiers"][0]["premium_reward"] == battlepass.tier_reward(1) * 3
        # non-sub NEsmí claimnout premium
        assert battlepass.claim(conn, non, 1, premium=True)["ok"] is False
        # sub claimne premium tier 1 → 3× odměna
        r = battlepass.claim(conn, sub, 1, premium=True)
        assert r["ok"] and r["premium"] and r["reward"] == battlepass.premium_reward(1)
        assert conn.execute("SELECT earned_total FROM users WHERE id=?", (uid,)).fetchone()["earned_total"] == 6000
        # znovu premium tier 1 → fail (už vyzvednuto)
        assert battlepass.claim(conn, sub, 1, premium=True)["ok"] is False
        # free řada tier 1 je nezávislá → pořád jde claimnout
        assert battlepass.claim(conn, sub, 1, premium=False)["ok"] is True
    finally:
        conn.close()
