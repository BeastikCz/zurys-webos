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
