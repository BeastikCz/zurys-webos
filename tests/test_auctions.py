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
        assert _pts(conn, u1) == 100000 - 10, "přehozenému vráceno 90 % (ztratil 10 ze 100)"
        assert _pts(conn, u2) == 100000 - 150
        assert not auctions.bid(conn, _row(conn, u1), aid, 150).get("ok"), "pod min (200) zamítnuto"
        assert _pts(conn, u1) == 100000 - 10, "zamítnutý příhoz (pod min) nic neodečte"
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


def test_buy_now_refunds_current_leader_not_old():
    """TOCTOU fix: buy_now vrací REÁLNÉMU aktuálnímu vůdci (z DB), ne starému ze snapshotu."""
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "BN", "", 100, 50, 10, buy_now=50000)["id"]
        u1, u2, u3 = _user(conn), _user(conn), _user(conn); conn.commit()
        auctions.bid(conn, _row(conn, u1), aid, 200)        # u1 vede 200
        auctions.bid(conn, _row(conn, u2), aid, 400)        # u2 přehodí → u1 dostane 90 % (180)
        assert _pts(conn, u1) == 100000 - 20
        assert _pts(conn, u2) == 100000 - 400
        r = auctions.buy_now(conn, _row(conn, u3), aid)     # u3 vykoupí
        assert r["ok"] and _pts(conn, u3) == 100000 - 50000
        assert _pts(conn, u2) == 100000, "aktuální vůdce u2 dostal 100 % zpět"
        assert _pts(conn, u1) == 100000 - 20, "starý (přehozený) vůdce NEDOSTANE nic navíc"
    finally:
        conn.close()


def test_bid_cannot_reach_buynow():
    """Příhoz >= kup-teď cena je zamítnut → current_bid vždy < buy_now (brání money-printu)."""
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "BN2", "", 100, 50, 10, buy_now=1000)["id"]
        u1 = _user(conn); conn.commit()
        assert not auctions.bid(conn, _row(conn, u1), aid, 1000).get("ok"), "příhoz = buy_now zamítnut"
        assert not auctions.bid(conn, _row(conn, u1), aid, 1500).get("ok"), "příhoz > buy_now zamítnut"
        assert _pts(conn, u1) == 100000, "zamítnutý příhoz nic neodečte"
        assert auctions.bid(conn, _row(conn, u1), aid, 900)["ok"], "příhoz < buy_now OK"
    finally:
        conn.close()


def test_buynow_rejected_when_bid_reached_price():
    """Guard: když příhoz dosáhl/přesáhl kup-teď cenu, buy_now je zamítnut + escrow vrácen."""
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "BN3", "", 100, 50, 10, buy_now=1000)["id"]
        u1, u2 = _user(conn), _user(conn); conn.commit()
        auctions.bid(conn, _row(conn, u1), aid, 900)
        conn.execute("UPDATE auctions SET current_bid = 1000 WHERE id = ?", (aid,)); conn.commit()  # simuluj dosažení ceny
        r = auctions.buy_now(conn, _row(conn, u2), aid)
        assert not r.get("ok"), "kup-teď zamítnut když příhoz >= cena"
        assert _pts(conn, u2) == 100000, "escrow vrácen (kup teď selhal)"
    finally:
        conn.close()


def test_cancel_refunds_current_leader_after_outbid():
    """TOCTOU fix: cancel vrací 100 % AKTUÁLNÍMU vůdci (z DB), ne přehozenému ze snapshotu."""
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "C", "", 100, 50, 10)["id"]
        u1, u2 = _user(conn), _user(conn); conn.commit()
        auctions.bid(conn, _row(conn, u1), aid, 300)
        auctions.bid(conn, _row(conn, u2), aid, 600)        # u2 vede, u1 dostal 90 % (270)
        r = auctions.cancel(conn, aid)
        assert r["ok"] and r["refunded"] == 600
        assert _pts(conn, u2) == 100000, "aktuální vůdce u2 dostal 100 % zpět"
        assert _pts(conn, u1) == 100000 - 30, "u1 zůstává na 90 % z přehození (cancel mu nic navíc nedává)"
    finally:
        conn.close()


def test_image_url_sanitized():
    """image_url do CSS url() – breakout znaky pryč, nepovolené schéma zahozeno."""
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        a1 = auctions.create(conn, "X1", "https://cdn.example.com/skin.png", 100, 50, 10)["id"]
        assert conn.execute("SELECT image_url FROM auctions WHERE id=?", (a1,)).fetchone()["image_url"] \
            == "https://cdn.example.com/skin.png", "čistá URL projde beze změny"
        a2 = auctions.create(conn, "X2", "https://x.com/a.png'); background:url(evil)", 100, 50, 10)["id"]
        u2 = conn.execute("SELECT image_url FROM auctions WHERE id=?", (a2,)).fetchone()["image_url"]
        assert not any(ch in u2 for ch in "'\"()<> "), f"breakout znaky odstraněny: {u2}"
        a3 = auctions.create(conn, "X3", "javascript:alert(1)", 100, 50, 10)["id"]
        assert conn.execute("SELECT image_url FROM auctions WHERE id=?", (a3,)).fetchone()["image_url"] == "", \
            "nepovolené schéma zahozeno"
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


def test_update_and_delete():
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "Upd", "", 100, 50, 10)["id"]
        u1 = _user(conn); conn.commit()
        # bez příhozů jde měnit i vyvolávací cena
        assert auctions.update(conn, aid, {"title": "Upd2", "start_bid": 200, "buy_now": 5000})["ok"]
        a = conn.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
        assert a["title"] == "Upd2" and a["start_bid"] == 200 and a["buy_now"] == 5000
        auctions.bid(conn, _row(conn, u1), aid, 300)
        assert not auctions.update(conn, aid, {"start_bid": 500})["ok"], "start_bid po příhozu zamčený"
        assert not auctions.update(conn, aid, {"buy_now": 300})["ok"], "kup-teď <= aktuální příhoz zamítnut"
        assert auctions.update(conn, aid, {"min_increment": 99, "sub_only": True})["ok"]
        # delete: aktivní ne, po zrušení ano
        assert not auctions.delete(conn, aid)["ok"], "aktivní nejde smazat"
        assert auctions.cancel(conn, aid)["ok"]
        assert _pts(conn, u1) == 100000, "escrow vrácen při zrušení"
        assert auctions.delete(conn, aid)["ok"]
        assert conn.execute("SELECT 1 FROM auctions WHERE id=?", (aid,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM auction_bids WHERE auction_id=?", (aid,)).fetchone() is None
    finally:
        conn.close()
