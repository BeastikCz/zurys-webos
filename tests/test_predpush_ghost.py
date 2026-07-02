"""Predikce web push (cooldown) + ghost claim ping + quests_detail v /me/claims.

    .venv/Scripts/python.exe -m pytest tests/test_predpush_ghost.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta


def _mk_user(conn, points=500, kick_id=None):
    from app.db import now_iso
    u = f"g_{secrets.token_hex(3)}"
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, kick_id, created_at) VALUES (?,?,?,?,?,?)",
        (u, u, "user", points, kick_id, now_iso())).lastrowid
    conn.commit()
    return uid, u


def test_predpush_cooldown(client, monkeypatch):
    from app.db import get_conn, set_setting
    from app.routers import predictions
    from app import webpush
    calls = []
    monkeypatch.setattr(webpush, "broadcast_async", lambda *a, **k: calls.append(a))
    conn = get_conn()
    try:
        set_setting(conn, "predpush_last", "")           # čistý stav
        conn.commit()
        assert predictions._maybe_push_new_pred(conn, "Vyhrajeme?") is True
        assert predictions._maybe_push_new_pred(conn, "A teď?") is False   # cooldown okno
        assert len(calls) == 1
        # starý timestamp za oknem → push znovu projde
        old = (datetime.now(timezone.utc) - timedelta(seconds=predictions.PREDPUSH_COOLDOWN_S + 60)).isoformat()
        set_setting(conn, "predpush_last", old)
        conn.commit()
        assert predictions._maybe_push_new_pred(conn, "Po pauze?") is True
        assert len(calls) == 2
    finally:
        conn.close()


def test_ghost_ping_once_per_user(client):
    from app.db import get_conn
    from app import kickevents
    conn = get_conn()
    try:
        uid, uname = _mk_user(conn, points=5000, kick_id=None)      # ghost s body
        kickevents._ghost_ping_last = 0.0
        msg = kickevents._ghost_claim_ping(conn, uname.upper())      # case-insensitive lookup
        assert msg and "5 000" in msg and uname in msg
        conn.commit()
        kickevents._ghost_ping_last = 0.0                            # obejdi globální rozestup
        assert kickevents._ghost_claim_ping(conn, uname) is None     # 1× za život
    finally:
        conn.close()


def test_ghost_ping_filters(client):
    import time
    from app.db import get_conn
    from app import kickevents
    conn = get_conn()
    try:
        kickevents._ghost_ping_last = 0.0
        _, poor = _mk_user(conn, points=200, kick_id=None)           # málo bodů
        assert kickevents._ghost_claim_ping(conn, poor) is None
        _, real = _mk_user(conn, points=9999, kick_id="12345")       # reálný účet (přihlášený)
        assert kickevents._ghost_claim_ping(conn, real) is None
        _, rich = _mk_user(conn, points=9999, kick_id=None)
        kickevents._ghost_ping_last = time.time()                    # globální rozestup drží
        assert kickevents._ghost_claim_ping(conn, rich) is None
        kickevents._ghost_ping_last = 0.0
        assert kickevents._ghost_claim_ping(conn, rich) is not None  # po rozestupu projde
        conn.commit()
    finally:
        conn.close()


def test_claims_quests_detail(client):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        uid, _ = _mk_user(conn)
        tok = f"tok_{secrets.token_hex(8)}"
        exp = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, uid, now_iso(), exp))
        conn.commit()
        d = client.get("/api/me/claims", cookies={"webos_session": tok}).json()
        qd = d["quests_detail"]
        assert isinstance(qd, list) and len(qd) >= 1
        q = qd[0]
        for k in ("key", "name", "desc", "reward", "progress", "target", "completed", "claimed"):
            assert k in q
        assert all(x["progress"] <= x["target"] for x in qd)
    finally:
        conn.close()
