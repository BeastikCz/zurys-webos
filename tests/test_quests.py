"""Questy: baseline/diff postup + claim odměny (server ověří splnění).

    .venv/Scripts/python.exe -m pytest tests/test_quests.py -v
"""
import secrets

import pytest

from app import quests
from app.db import get_conn, now_iso


def _make_user(conn, points=0):
    uname = f"q_{secrets.token_hex(4)}"
    cur = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
        (uname, uname, "user", points, now_iso()))
    return cur.lastrowid


def test_period_id_formats():
    assert "-W" in quests._period_id("weekly")
    d = quests._period_id("daily")
    assert len(d) == 10 and d.count("-") == 2          # YYYY-MM-DD


def test_quest_progress_and_claim(client):
    conn = get_conn()
    try:
        uid = _make_user(conn)
        conn.commit()
        # první načtení založí baseline (drops = 0)
        d_drop = next(q for q in quests.get_quests(conn, uid) if q["key"] == "d_drop")
        assert d_drop["progress"] == 0 and not d_drop["completed"]

        # claim nesplněného → chyba
        with pytest.raises(ValueError):
            quests.claim_quest(conn, uid, "d_drop")

        # simuluj chycení dropů (stat 'drops' = COUNT drop_claims) – kolik je třeba dle targetu
        d_drop_target = next(q["target"] for q in quests.QUESTS if q["key"] == "d_drop")
        for i in range(d_drop_target):
            drop = conn.execute(
                "INSERT INTO drops (code, points, max_winners, active, created_at) VALUES (?,100,1,1,?)",
                (f"Q{i}", now_iso())).lastrowid
            conn.execute("INSERT INTO drop_claims (drop_id, user_id, position, created_at) VALUES (?,?,1,?)",
                         (drop, uid, now_iso()))
        conn.commit()

        d_drop2 = next(q for q in quests.get_quests(conn, uid) if q["key"] == "d_drop")
        assert d_drop2["progress"] == d_drop_target and d_drop2["completed"]

        # claim splněného → +100 (vyvážená odměna d_drop)
        before = conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
        res = quests.claim_quest(conn, uid, "d_drop")
        after = conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
        assert after - before == 100 and res["reward"] == 100

        # druhý claim → chyba (už vyzvednuto)
        with pytest.raises(ValueError):
            quests.claim_quest(conn, uid, "d_drop")
    finally:
        conn.close()


def test_chat_quest_progress(client):
    """Chat quest postupuje podle bodů z 'Aktivita v chatu' (anti-spam metrika)."""
    conn = get_conn()
    try:
        uid = _make_user(conn)
        conn.commit()
        d_chat = next(q for q in quests.get_quests(conn, uid) if q["key"] == "d_chat")
        assert d_chat["progress"] == 0 and not d_chat["completed"]
        d_chat_target = next(q["target"] for q in quests.QUESTS if q["key"] == "d_chat")
        for _ in range(d_chat_target):   # cooldown-gated odměny za chat dle targetu
            conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                         (uid, 1, "Aktivita v chatu", now_iso()))
        conn.commit()
        d_chat2 = next(q for q in quests.get_quests(conn, uid) if q["key"] == "d_chat")
        assert d_chat2["progress"] == d_chat_target and d_chat2["completed"]
    finally:
        conn.close()


def test_earn_quest_counts_only_stream_activity(client):
    """'earned' quest postupuje JEN z bodů za sledování/chat na streamu.

    Admin granty, dárky, výhry ani odměny za jiné questy/odznaky se NEsmí počítat –
    jinak by se „Vydělej …" splnil i bez streamu (to byl původní bug).
    """
    conn = get_conn()
    try:
        uid = _make_user(conn)
        conn.commit()
        d_earn = next(q for q in quests.get_quests(conn, uid) if q["key"] == "d_earn")
        assert d_earn["progress"] == 0 and not d_earn["completed"]
        target = d_earn["target"]

        # NEstreamové body (i hodně) → NEPOČÍTAJÍ se do questu
        for reason in ("Úprava adminem", "Úkol: Něco 📋", "Odznak: OG", "Výhra v duelu", "Dárek"):
            conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                         (uid, target + 500, reason, now_iso()))
        conn.commit()
        d2 = next(q for q in quests.get_quests(conn, uid) if q["key"] == "d_earn")
        assert d2["progress"] == 0 and not d2["completed"], "ne-stream body se nesmí počítat do 'earned'"

        # Body ze streamu (sledování + chat) → počítají se
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, target - 1, "Sledování streamu", now_iso()))
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 1, "Aktivita v chatu", now_iso()))
        conn.commit()
        d3 = next(q for q in quests.get_quests(conn, uid) if q["key"] == "d_earn")
        assert d3["progress"] >= target and d3["completed"], "stream body (sledování+chat) se musí počítat"
    finally:
        conn.close()


def test_quests_endpoint_offline(client, monkeypatch):
    """Mimo provoz (QUESTS_ENABLED=False): /quests vrátí [] a claim se zamítne (400)."""
    from datetime import datetime, timezone, timedelta
    from app.config import SESSION_COOKIE
    monkeypatch.setattr(quests, "QUESTS_ENABLED", False)
    conn = get_conn()
    try:
        uname = f"qoff_{secrets.token_hex(4)}"
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (uname, uname, "user", now_iso()))
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
    finally:
        conn.close()
    hdr = {"Cookie": f"{SESSION_COOKIE}={token}"}
    r = client.get("/api/quests", headers=hdr)
    assert r.status_code == 200 and r.json() == [], f"/quests měl vrátit [], dal {r.status_code}: {r.text}"
    r2 = client.post("/api/quests/claim", json={"key": "d_drop"}, headers=hdr)
    assert r2.status_code == 400, f"claim měl být zamítnut (400), byl {r2.status_code}"


def test_unknown_quest_key(client):
    conn = get_conn()
    try:
        uid = _make_user(conn)
        conn.commit()
        with pytest.raises(ValueError):
            quests.claim_quest(conn, uid, "neexistuje")
    finally:
        conn.close()
