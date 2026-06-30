"""Aukce o skiny: escrow příhoz, přehození (vrácení), min příhoz, finalizace (vítěz=sink), zrušení, anti-snipe.

    .venv/Scripts/python.exe -m pytest tests/test_auctions.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta


def _user(conn, points=100000):
    from app.db import now_iso
    u = f"auc_{secrets.token_hex(3)}"
    return conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
                        (u, u, "user", points, now_iso())).lastrowid


def _row(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _pts(conn, uid):
    return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]


def test_escrow_outbid_min_and_self():
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "Test skin", "", 100, 50, 10)["id"]
        u1, u2 = _user(conn), _user(conn); conn.commit()
        r = auctions.bid(conn, _row(conn, u1), aid, 100)
        assert r["ok"] and r["current_bid"] == 100
        assert _pts(conn, u1) == 100000 - 100, "escrow odečetl příhoz"
        r2 = auctions.bid(conn, _row(conn, u2), aid, 150)            # přehoz
        assert r2["ok"]
        assert _pts(conn, u1) == 100000 - 50, "přehozenému vráceno jen 50 % (ztratil 50 ze 100)"
        assert _pts(conn, u2) == 100000 - 150
        assert not auctions.bid(conn, _row(conn, u1), aid, 150).get("ok"), "pod min (200) zamítnuto"
        assert _pts(conn, u1) == 100000 - 50, "zamítnutý příhoz (pod min) nic neodečte"
        assert not auctions.bid(conn, _row(conn, u2), aid, 400).get("ok"), "vedoucí nesmí přehodit sám sebe"
    finally:
        conn.close()


def test_finalize_winner_is_sink():
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "Skin2", "", 100, 50, 10)["id"]
        u1 = _user(conn); conn.commit()
        auctions.bid(conn, _row(conn, u1), aid, 500)
        assert _pts(conn, u1) == 100000 - 500
        # přetoč konec do minulosti → list_public finalizuje
        conn.execute("UPDATE auctions SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?", (aid,)); conn.commit()
        auctions.list_public(conn)
        a = conn.execute("SELECT status, winner_id FROM auctions WHERE id=?", (aid,)).fetchone()
        assert a["status"] == "ended" and a["winner_id"] == u1, "vítěz = poslední vedoucí"
        assert _pts(conn, u1) == 100000 - 500, "vítězovy sedláci zůstaly odečtené (sink)"
        # po skončení už nejde přihodit
        assert not auctions.bid(conn, _row(conn, _user(conn)), aid, 1000).get("ok")
    finally:
        conn.close()


def test_cancel_refunds_leader():
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "Skin3", "", 100, 50, 10)["id"]
        u1 = _user(conn); conn.commit()
        auctions.bid(conn, _row(conn, u1), aid, 300)
        assert _pts(conn, u1) == 100000 - 300
        r = auctions.cancel(conn, aid)
        assert r["ok"] and r["refunded"] == 300
        assert _pts(conn, u1) == 100000, "zrušení vrátilo vůdci sedláky"
        assert conn.execute("SELECT status FROM auctions WHERE id=?", (aid,)).fetchone()["status"] == "cancelled"
    finally:
        conn.close()


def test_antisnipe_extends():
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "Skin4", "", 100, 50, 10)["id"]
        u1 = _user(conn); conn.commit()
        # nastav konec na +20 s (< ANTISNIPE_SEC 30) → příhoz prodlouží
        soon = (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()
        conn.execute("UPDATE auctions SET ends_at=? WHERE id=?", (soon, aid)); conn.commit()
        r = auctions.bid(conn, _row(conn, u1), aid, 100)
        assert r["ok"] and r["extended"] is True, "anti-snipe prodloužil konec"
        new_end = conn.execute("SELECT ends_at FROM auctions WHERE id=?", (aid,)).fetchone()["ends_at"]
        left = (datetime.fromisoformat(new_end) - datetime.now(timezone.utc)).total_seconds()
        assert 25 <= left <= 31, f"konec ~+30 s, je {left:.0f}"
    finally:
        conn.close()
