"""Achievementy: výpočet tieru + scanner uděluje/povyšuje odznaky (idempotentně).

    .venv/Scripts/python.exe -m pytest tests/test_achievements.py -v
"""
import secrets

from app import achievements
from app.db import get_conn, now_iso


def test_earned_tier():
    assert achievements._earned_tier([10, 50, 100], 60) == 2
    assert achievements._earned_tier([10, 50, 100], 9) == 0
    assert achievements._earned_tier([10, 50, 100], 100) == 3
    assert achievements._earned_tier([1], 1) == 1
    assert achievements._earned_tier([1], 0) == 0


def test_scan_awards_absolute_badges(client):
    """Odznaky z absolutních statů (balance/streak/flag) – nezávislé na ostatních."""
    conn = get_conn()
    try:
        uname = f"ach_{secrets.token_hex(4)}"
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, daily_streak, is_sub, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (uname, uname, "user", 150000, 40, 1, now_iso()))
        uid = cur.lastrowid
        conn.commit()

        achievements.scan_and_award(conn)
        got = {r["badge_key"]: r["tier"] for r in conn.execute(
            "SELECT badge_key, tier FROM user_badges WHERE user_id=?", (uid,))}
        assert got.get("rich") == 1, got            # 150k >= 100k
        assert got.get("loyal") == 2, got           # streak 40 → tiers [7,30,100] → 2
        assert got.get("sub") == 1, got

        # idempotentní re-sken: žádné snížení tieru, žádná chyba
        achievements.scan_and_award(conn)
        got2 = {r["badge_key"]: r["tier"] for r in conn.execute(
            "SELECT badge_key, tier FROM user_badges WHERE user_id=?", (uid,))}
        assert got2 == got
    finally:
        conn.close()


def test_scan_promotes_tier(client):
    """Když stat naroste přes vyšší práh, tier se povýší (ne duplicitní řádek)."""
    conn = get_conn()
    try:
        uname = f"ach_{secrets.token_hex(4)}"
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, daily_streak, created_at) "
            "VALUES (?,?,?,?,?,?)", (uname, uname, "user", 0, 7, now_iso()))
        uid = cur.lastrowid
        conn.commit()
        achievements.scan_and_award(conn)
        t1 = conn.execute("SELECT tier FROM user_badges WHERE user_id=? AND badge_key='loyal'", (uid,)).fetchone()
        assert t1 and t1["tier"] == 1               # streak 7 → tier 1

        conn.execute("UPDATE users SET daily_streak=30 WHERE id=?", (uid,))
        conn.commit()
        achievements.scan_and_award(conn)
        rows = conn.execute("SELECT tier FROM user_badges WHERE user_id=? AND badge_key='loyal'", (uid,)).fetchall()
        assert len(rows) == 1 and rows[0]["tier"] == 2   # povýšeno na tier 2, pořád 1 řádek
    finally:
        conn.close()


def test_rankup_silent_init_then_celebrates(client, monkeypatch):
    """1. sken = tichá inicializace ligy (žádné konfety). Postup do vyšší ligy
    teprve nastaví pending_rankup (frontu oslavy). Bot shoutout je no-op (mock)."""
    from app import kickbot
    monkeypatch.setattr(kickbot, "send_message", lambda *a, **k: None)
    conn = get_conn()
    try:
        uname = f"ru_{secrets.token_hex(4)}"
        # obří body → zaručeně #1 → liga 'unreal'
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (uname, uname, "user", 999999999, now_iso()))
        uid = cur.lastrowid
        conn.commit()

        achievements.scan_rankups(conn)             # tichá inicializace
        row = conn.execute("SELECT last_league, pending_rankup FROM users WHERE id=?", (uid,)).fetchone()
        assert row["last_league"] == "unreal"
        assert not row["pending_rankup"]            # žádná oslava při prvním běhu

        # simuluj, že byl dřív bez ligy → teď je postup
        conn.execute("UPDATE users SET last_league='', pending_rankup=NULL WHERE id=?", (uid,))
        conn.commit()
        achievements.scan_rankups(conn)
        row2 = conn.execute("SELECT last_league, pending_rankup FROM users WHERE id=?", (uid,)).fetchone()
        assert row2["last_league"] == "unreal"
        assert row2["pending_rankup"] == "unreal"   # fronta konfet nastavena
    finally:
        conn.close()


def test_overtake_sets_pending(client, monkeypatch):
    """Pád na žebříčku (z TOP 100) → pending_overtake s {by, rank}. 1. sken = tichý."""
    import json as _json
    from app import kickbot
    monkeypatch.setattr(kickbot, "send_message", lambda *a, **k: None)
    conn = get_conn()
    try:
        uname = f"ov_{secrets.token_hex(4)}"
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (uname, uname, "user", 500, now_iso()))   # málo bodů → NENÍ #1
        uid = cur.lastrowid
        conn.commit()

        achievements.scan_rankups(conn)             # tichá inicializace last_rank
        r1 = conn.execute("SELECT pending_overtake FROM users WHERE id=?", (uid,)).fetchone()
        assert not r1["pending_overtake"]           # 1. běh nic neoznamuje

        # předstírej, že byl dřív v TOP (#1) a teď spadl
        conn.execute("UPDATE users SET last_rank=1, pending_overtake=NULL WHERE id=?", (uid,))
        conn.commit()
        achievements.scan_rankups(conn)
        row = conn.execute("SELECT pending_overtake FROM users WHERE id=?", (uid,)).fetchone()
        assert row["pending_overtake"], "měla se nastavit hláška 'přeskočil tě'"
        o = _json.loads(row["pending_overtake"])
        assert "by" in o and o.get("rank", 0) > 1
    finally:
        conn.close()
