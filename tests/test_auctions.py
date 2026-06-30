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


def test_buy_now_instant_win():
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "BuyNowSkin", "", 100, 50, 10, buy_now=5000)["id"]
        u1, u2 = _user(conn), _user(conn); conn.commit()
        auctions.bid(conn, _row(conn, u1), aid, 200)            # u1 vede na 200
        assert _pts(conn, u1) == 100000 - 200
        r = auctions.buy_now(conn, _row(conn, u2), aid)         # u2 vykoupí
        assert r["ok"] and r["price"] == 5000
        assert _pts(conn, u2) == 100000 - 5000
        assert _pts(conn, u1) == 100000, "vykoupený vůdce dostal 100 % zpět"
        a = conn.execute("SELECT status, winner_id FROM auctions WHERE id=?", (aid,)).fetchone()
        assert a["status"] == "ended" and a["winner_id"] == u2
        assert not auctions.buy_now(conn, _row(conn, _user(conn)), aid).get("ok"), "po skončení už ne"
    finally:
        conn.close()


def test_sub_only_gate():
    from app.db import get_conn, now_iso
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "SubSkin", "", 100, 50, 10, sub_only=1)["id"]
        free = _user(conn)
        sub = conn.execute("INSERT INTO users (kick_username, username, role, points, is_sub, created_at) "
                           "VALUES (?,?,?,?,1,?)", (f"sb_{secrets.token_hex(3)}", "sb", "user", 100000, now_iso())).lastrowid
        conn.commit()
        assert not auctions.bid(conn, _row(conn, free), aid, 200).get("ok"), "non-sub nesmí na sub-only"
        assert auctions.bid(conn, _row(conn, sub), aid, 200)["ok"], "sub smí"
    finally:
        conn.close()


def test_top_bidders_and_going_once():
    from app.db import get_conn
    from app import auctions
    from datetime import datetime, timezone, timedelta
    conn = get_conn()
    try:
        u1 = _user(conn); conn.commit()
        # 2 aukce vyhrané u1 → žebříček
        for _ in range(2):
            aid = auctions.create(conn, "Skin", "", 100, 50, 10)["id"]
            auctions.bid(conn, _row(conn, u1), aid, 300)
            conn.execute("UPDATE auctions SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?", (aid,)); conn.commit()
            auctions.list_public(conn)
        tb = auctions.top_bidders(conn)
        top = next((x for x in tb if x["username"] == _row(conn, u1)["username"]), None)
        assert top and top["wins"] == 2, f"u1 má 2 výhry, žebříček: {tb}"
        # going_once flag: aukce končící za 20 s + příhoz → list_public nastaví flag 1×
        aid2 = auctions.create(conn, "GoSkin", "", 100, 50, 10)["id"]
        auctions.bid(conn, _row(conn, u1), aid2, 200)
        soon = (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()
        conn.execute("UPDATE auctions SET ends_at=? WHERE id=?", (soon, aid2)); conn.commit()
        auctions.list_public(conn)
        assert conn.execute("SELECT going_once_sent FROM auctions WHERE id=?", (aid2,)).fetchone()[0] == 1, "going_once nastaveno"
        auctions.list_public(conn)   # podruhé neopakuje (flag drží) – jen ověř že nespadne
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
