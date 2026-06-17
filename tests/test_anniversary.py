"""Výročí: daemon vyplatí JEN nově překročený milník (1×), žádný zpětný backfill. + síň slávy.

    .venv/Scripts/python.exe -m pytest tests/test_anniversary.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _user(days_ago):
    from app.db import get_conn
    conn = get_conn()
    try:
        u = f"anniv_{secrets.token_hex(4)}"
        created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (u, u, "user", 0, created)).lastrowid
        conn.commit()
        return uid
    finally:
        conn.close()


def _pts(uid):
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()


def test_anniversary_awards_recent_milestone_once(client):
    from app import anniversary
    fresh = _user(31)    # právě překročil 1 měsíc (v grace okně)
    young = _user(5)     # nováček – nic
    old = _user(400)     # dávno překročil 30/90/180/365 → žádný backfill

    anniversary._run_once()
    assert _pts(fresh) == 500, "31denní účet bere bonus za 1 měsíc"
    assert _pts(young) == 0, "5denní nic"
    assert _pts(old) == 0, "starý účet (dávno překročené milníky) = žádný backfill"

    anniversary._run_once()                  # idempotence
    assert _pts(fresh) == 500, "každý milník max 1×"


def test_hall_of_fame_shape(client):
    r = client.get("/api/hall-of-fame")
    assert r.status_code == 200
    d = r.json()
    for k in ("loyal", "subs", "gifters", "active"):
        assert k in d and isinstance(d[k], list)


def test_hall_of_fame_gifters_counts_subs(client):
    """Nejštědřejší: subs = součet darovaných subů z reason „… ×N" (i s happy-hour příponou)."""
    import secrets
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        u = f"gf_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (u, u, "user", now_iso())).lastrowid
        # 2 gift eventy: ×9000 (běžné) + ×123 (happy 2×). subs=9123, metric=9 000 000+246 000.
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 9_000_000, "Kick gift sub 🎁 ×9000", now_iso()))
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 246_000, "Kick gift sub 🎁 ×123 (happy 2×)", now_iso()))
        # příjemce se NEsmí počítat
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 0, "Kick gift sub (příjemce)", now_iso()))
        conn.commit()
    finally:
        conn.close()
    from app.routers import misc
    misc._hof_cache["data"] = None     # vynuluj cache → čerstvá data vč. nového giftera
    g = client.get("/api/hall-of-fame").json()["gifters"]
    me = next((x for x in g if x["username"] == u), None)
    assert me is not None, "gifter má být v žebříčku (×9000 = rank 1)"
    assert me["subs"] == 9123, f"subs měl být 9123, je {me['subs']}"
    assert me["metric"] == 9_246_000
