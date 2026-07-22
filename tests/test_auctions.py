"""Aukce o skiny: escrow příhoz, přehození (vrácení), min příhoz, finalizace (vítěz=sink), zrušení, anti-snipe.

    .venv/Scripts/python.exe -m pytest tests/test_auctions.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.config import SESSION_COOKIE


def _user(conn, points=100000):
    from app.db import now_iso
    u = f"auc_{secrets.token_hex(3)}"
    return conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
                        (u, u, "user", points, now_iso())).lastrowid


def _row(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _pts(conn, uid):
    return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]


def _session(conn, uid):
    from app.db import now_iso
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
                 (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def test_market_duration_selector_is_wired():
    source = Path("web/app.js").read_text(encoding="utf-8")
    assert 'id="market_duration"' in source
    assert 'value="360">6 hodin' in source
    assert 'value="10080">7 dní' in source
    assert 'duration_minutes: parseInt(document.getElementById("market_duration")' in source


def test_market_submission_requires_admin_approval(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        seller_id = _user(conn)
        admin_id = _user(conn)
        conn.execute("UPDATE users SET role='admin' WHERE id=?", (admin_id,))
        seller_name = _row(conn, seller_id)["username"]
        seller_headers, admin_headers = _session(conn, seller_id), _session(conn, admin_id)
        conn.commit()
    finally:
        conn.close()

    payload = {"title": "M4A1-S | Printstream (FT)", "image_url": "https://example.com/skin.png",
               "description": "(FT) · float 0.21", "inspect_url": "steam://rungame/730/test",
               "wear": "FN", "float_value": 0.21, "price": 25000, "sale_type": "fixed",
               "duration_minutes": 4320}
    submitted = client.post("/api/auctions/submissions", json=payload, headers=seller_headers)
    assert submitted.status_code == 200, submitted.text
    sid = submitted.json()["id"]
    assert all(a["title"] != payload["title"] for a in client.get("/api/auctions").json()["active"])

    queue = client.get("/api/admin/auctions/submissions", headers=admin_headers)
    assert queue.status_code == 200 and any(
        s["id"] == sid and s["username"] == seller_name and s["sale_type"] == "fixed"
        and s["duration_minutes"] == 4320
        and s["description"] == payload["description"] and s["wear"] == "FT" and s["float_value"] == 0.21
        for s in queue.json()["pending"])
    approved = client.post(f"/api/admin/auctions/submissions/{sid}/approve", headers=admin_headers)
    assert approved.status_code == 200, approved.text
    conn = get_conn()
    try:
        from app import auctions
        public = conn.execute("SELECT * FROM auctions WHERE id=?", (approved.json()["auction_id"],)).fetchone()
        assert public["seller_user_id"] == seller_id and public["start_bid"] == 25000
        assert public["buy_now"] == 25000 and public["sale_type"] == "fixed"
        remaining = (datetime.fromisoformat(public["ends_at"]) - datetime.now(timezone.utc)).total_seconds()
        assert 4310 * 60 <= remaining <= 4320 * 60
        assert public["market_description"] == payload["description"] and public["wear"] == "FT"
        assert public["float_value"] == 0.21
        listing = next(a for a in auctions.list_public(conn)["active"] if a["id"] == public["id"])
        assert listing["description"] == payload["description"] and listing["wear"] == "FT"
        assert listing["float_value"] == 0.21 and listing["seller_completed_sales"] == 0
        buyer_id = _user(conn)
        buyer = _row(conn, buyer_id)
        buyer_name = buyer["username"]
        outsider_id = _user(conn)
        buyer_headers, outsider_headers = _session(conn, buyer_id), _session(conn, outsider_id)
        conn.execute("UPDATE users SET steam_trade_url=? WHERE id=?",
                     ("https://steamcommunity.com/tradeoffer/new/?partner=123&token=test", buyer_id))
        conn.commit()
        assert not auctions.bid(conn, buyer, public["id"], 25000)["ok"]
        assert auctions.buy_now(conn, buyer, public["id"])["ok"]
        assert _pts(conn, seller_id) == 100000, "prodávající před potvrzením nedostal escrow"
    finally:
        conn.close()

    invalid_wear = client.post(
        "/api/auctions/submissions", json={**payload, "title": "Invalid wear", "wear": "BROKEN"},
        headers=seller_headers,
    )
    assert invalid_wear.status_code == 422
    invalid_duration = client.post(
        "/api/auctions/submissions", json={**payload, "title": "Invalid duration", "duration_minutes": 10081},
        headers=seller_headers,
    )
    assert invalid_duration.status_code == 422
    mine = client.get("/api/auctions/my-sales", headers=seller_headers)
    assert mine.status_code == 200
    sale = mine.json()["sales"][0]
    assert sale["id"] == public["id"] and sale["delivery_status"] == "awaiting_delivery"
    assert sale["seller_paid_at"] is None and sale["wear"] == "FT" and sale["float_value"] == 0.21
    assert sale["winner"] == buyer_name and sale["winner_trade_url"].startswith("https://steamcommunity.com/tradeoffer/")
    assert sale["winner_completed_purchases"] == 0
    buyer_deals = client.get("/api/auctions/my-sales", headers=buyer_headers).json()
    assert buyer_deals["sales"] == []
    assert buyer_deals["purchases"][0]["id"] == public["id"]
    assert buyer_deals["purchases"][0]["seller"] == seller_name
    assert buyer_deals["purchases"][0]["seller_completed_sales"] == 0
    assert client.get("/api/auctions/my-sales", headers=admin_headers).json() == {"sales": [], "purchases": []}
    delivery_url = f"/api/auctions/{public['id']}/delivery"
    assert client.post(f"{delivery_url}/confirm", headers=buyer_headers).status_code == 400
    assert client.post(f"{delivery_url}/sent", headers=outsider_headers).status_code == 400
    assert client.post(f"{delivery_url}/sent", headers=seller_headers).status_code == 200
    confirmed = client.post(f"{delivery_url}/confirm", headers=buyer_headers)
    assert confirmed.status_code == 200 and confirmed.json()["seller_payout"] == 23750
    conn = get_conn()
    try:
        assert _pts(conn, seller_id) == 123750
    finally:
        conn.close()
    assert client.get("/api/auctions/my-sales", headers=seller_headers).json()["sales"][0]["delivery_status"] == "completed"

    chat_url = f"/api/auctions/{public['id']}/chat"
    assert client.get(chat_url).status_code == 401
    assert client.get(chat_url, headers=outsider_headers).status_code == 403
    seller_chat = client.get(chat_url, headers=seller_headers)
    assert seller_chat.status_code == 200 and seller_chat.json()["can_send"] is True
    assert seller_chat.json()["messages"] == []
    assert seller_chat.json()["seller"]["completed_trades"] == 1
    assert seller_chat.json()["winner"]["completed_trades"] == 1
    admin_chat = client.get(chat_url, headers=admin_headers)
    assert admin_chat.status_code == 200 and admin_chat.json()["can_send"] is False
    assert client.post(chat_url, json={"body": "admin nesmí psát"}, headers=admin_headers).status_code == 403

    sent = client.post(chat_url, json={"body": "Pošlu ti skin přes tvoji Trade URL."}, headers=seller_headers)
    assert sent.status_code == 200
    buyer_chat = client.get(chat_url, headers=buyer_headers).json()
    assert buyer_chat["messages"][0]["from_name"] == seller_name
    assert buyer_chat["messages"][0]["mine"] is False
    assert client.post(chat_url, json={"body": "Díky, čekám."}, headers=buyer_headers).status_code == 200
    seller_messages = client.get(chat_url, headers=seller_headers).json()["messages"]
    assert [m["mine"] for m in seller_messages] == [True, False]
    assert len(client.get(chat_url, headers=admin_headers).json()["messages"]) == 2
    assert client.post(f"/api/admin/auctions/submissions/{sid}/approve", headers=admin_headers).status_code == 400
    assert client.post("/api/auctions/upload-image", json={"data": "x"}, headers=seller_headers).status_code in (404, 405)

    auction_payload = {**payload, "title": "AK-47 | Redline (FT)", "sale_type": "auction", "price": 12000}
    auction_submission = client.post("/api/auctions/submissions", json=auction_payload, headers=seller_headers)
    assert auction_submission.status_code == 200
    auction_approved = client.post(
        f"/api/admin/auctions/submissions/{auction_submission.json()['id']}/approve", headers=admin_headers)
    assert auction_approved.status_code == 200
    conn = get_conn()
    try:
        auction = conn.execute("SELECT * FROM auctions WHERE id=?", (auction_approved.json()["auction_id"],)).fetchone()
        assert auction["sale_type"] == "auction" and auction["start_bid"] == 12000 and auction["buy_now"] == 0
    finally:
        conn.close()


def test_market_ui_carries_and_escapes_description_and_wear():
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    router = (Path(__file__).parents[1] / "app" / "routers" / "auctions.py").read_text(encoding="utf-8")
    assert 'id="market_wear"' in source
    assert 'wear: document.getElementById("market_wear")?.value || ""' in source
    assert 'class="market-card-description"' in source
    assert '${esc(a.description)}' in source
    assert 'id="market_float"' in source and "marketWearFromFloat" in source
    assert 'data-market-filter="wear"' in source and "seller_completed_sales" in source
    assert 'data-action="market-delivery"' in source and 'data-action="market-dispute"' in source
    assert "marketTrustHTML" in source and "Trust Factor vychází" in source
    assert "market-soon" not in source and "Trh spustíme brzy" not in router


def test_market_float_boundaries():
    from app.auctions import wear_from_float
    assert [wear_from_float(v) for v in (0, 0.069999, 0.07, 0.15, 0.38, 0.45, 1)] == [
        "FN", "FN", "MW", "FT", "WW", "BS", "BS",
    ]


def test_no_bid_market_auction_has_no_escrow_actions():
    from app import auctions
    from app.db import get_conn
    conn = get_conn()
    try:
        seller_id = _user(conn)
        seller = _row(conn, seller_id)
        conn.commit()
        aid = auctions.create(conn, "No bids", "", 1000, 50, 10,
                              seller_username=seller["username"], sale_type="auction")["id"]
        conn.execute("UPDATE auctions SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?", (aid,))
        conn.commit()
        row = next(a for a in auctions.admin_list(conn) if a["id"] == aid)
        assert row["status"] == "ended" and row["who"] is None and row["current_bid"] == 0
        denied = auctions.resolve_delivery(conn, aid, "refund")
        assert not denied["ok"] and "bez kupujícího" in denied["error"]
        source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
        assert 'const sold = a.status === "ended" && !!a.who;' in source
        assert 'a.seller && sold' in source and "⚪ nevydraženo" in source
    finally:
        conn.close()


def test_market_dispute_admin_refund(client):
    from app import auctions
    from app.db import get_conn
    conn = get_conn()
    try:
        seller_id, buyer_id, outsider_id, admin_id = (_user(conn) for _ in range(4))
        conn.execute("UPDATE users SET role='admin' WHERE id=?", (admin_id,))
        seller_name = _row(conn, seller_id)["username"]
        seller_headers = _session(conn, seller_id)
        buyer_headers = _session(conn, buyer_id)
        outsider_headers = _session(conn, outsider_id)
        admin_headers = _session(conn, admin_id)
        conn.commit()
        aid = auctions.create(conn, "Disputed skin", "", 10000, 500, 10,
                              seller_username=seller_name, sale_type="auction")["id"]
        assert auctions.bid(conn, _row(conn, buyer_id), aid, 10000)["ok"]
        conn.execute("UPDATE auctions SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?", (aid,))
        conn.commit()
        auctions.list_public(conn)
        assert _pts(conn, buyer_id) == 89000 and _pts(conn, seller_id) == 100000
    finally:
        conn.close()

    dispute_url = f"/api/auctions/{aid}/delivery/dispute"
    chat_url = f"/api/auctions/{aid}/chat"
    assert client.get(chat_url, headers=admin_headers).json()["can_send"] is False
    assert client.post(chat_url, json={"body": "Předčasná zpráva"}, headers=admin_headers).status_code == 403
    assert client.post(dispute_url, json={"body": "cizí účet"}, headers=outsider_headers).status_code == 400
    disputed = client.post(dispute_url, json={"body": "Skin nebyl doručen."}, headers=buyer_headers)
    assert disputed.status_code == 200 and disputed.json()["delivery_status"] == "disputed"
    assert client.get(chat_url, headers=admin_headers).json()["can_send"] is True
    assert client.post(chat_url, json={"body": "Admin: prověřuji předání."}, headers=admin_headers).status_code == 200
    seller_chat = client.get(chat_url, headers=seller_headers).json()
    buyer_chat = client.get(chat_url, headers=buyer_headers).json()
    assert seller_chat["messages"][-1]["from_role"] == "admin"
    assert buyer_chat["messages"][-1]["body"] == "Admin: prověřuji předání."
    assert client.post(f"/api/auctions/{aid}/delivery/sent", headers=seller_headers).status_code == 400
    refunded = client.post(f"/api/admin/auctions/{aid}/market-refund", headers=admin_headers)
    assert refunded.status_code == 200 and refunded.json()["refunded"] == 11000
    assert client.post(f"/api/admin/auctions/{aid}/market-refund", headers=admin_headers).status_code == 400
    conn = get_conn()
    try:
        assert _pts(conn, buyer_id) == 100000 and _pts(conn, seller_id) == 100000
        row = conn.execute("SELECT delivery_status,seller_paid_at FROM auctions WHERE id=?", (aid,)).fetchone()
        assert row["delivery_status"] == "refunded" and row["seller_paid_at"] is None
    finally:
        conn.close()


def test_escrow_outbid_min_and_self():
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "Test skin", "", 100, 50, 10)["id"]
        u1, u2 = _user(conn), _user(conn); conn.commit()
        r = auctions.bid(conn, _row(conn, u1), aid, 100)
        assert r["ok"] and r["current_bid"] == 100 and r["fee"] == 10
        assert _pts(conn, u1) == 100000 - 110, "escrow odečetl příhoz + vstupní poplatek 10 %"
        r2 = auctions.bid(conn, _row(conn, u2), aid, 150)            # přehoz
        assert r2["ok"]
        assert _pts(conn, u1) == 100000 - 10, "přehozenému vráceno 100 % příhozu (zůstává jen poplatek 10)"
        assert _pts(conn, u2) == 100000 - 165
        assert not auctions.bid(conn, _row(conn, u1), aid, 150).get("ok"), "pod min (200) zamítnuto"
        assert _pts(conn, u1) == 100000 - 10, "zamítnutý příhoz (pod min) nic neodečte"
        assert not auctions.bid(conn, _row(conn, u2), aid, 400).get("ok"), "vedoucí nesmí přehodit sám sebe"
    finally:
        conn.close()


def test_entry_fee_once_capped_and_refunded_on_cancel():
    """Vstupní poplatek: 10 % z prvního příhozu se stropem 5k, další příhozy zdarma, zrušení vrací."""
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        aid = auctions.create(conn, "FeeSkin", "", 100, 50, 10)["id"]
        u1, u2 = _user(conn, points=300000), _user(conn, points=300000); conn.commit()
        r = auctions.bid(conn, _row(conn, u1), aid, 80000)          # 10 % = 8000 → strop 5000
        assert r["ok"] and r["fee"] == auctions.ENTRY_FEE_CAP
        assert _pts(conn, u1) == 300000 - 85000
        r2 = auctions.bid(conn, _row(conn, u2), aid, 90000)          # u1 zpět 100 % (80000)
        assert r2["ok"] and r2["fee"] == 5000
        assert _pts(conn, u1) == 300000 - 5000, "po přehození zůstává jen poplatek"
        r3 = auctions.bid(conn, _row(conn, u1), aid, 100000)         # druhý příhoz TÉHOŽ uživatele = bez poplatku
        assert r3["ok"] and r3["fee"] == 0
        assert _pts(conn, u1) == 300000 - 5000 - 100000
        auctions.cancel(conn, aid)
        assert _pts(conn, u1) == 300000 and _pts(conn, u2) == 300000, "zrušení vrací escrow i poplatky oběma"
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
        assert _pts(conn, u1) == 100000 - 550
        # přetoč konec do minulosti → list_public finalizuje
        conn.execute("UPDATE auctions SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?", (aid,)); conn.commit()
        auctions.list_public(conn)
        a = conn.execute("SELECT status, winner_id FROM auctions WHERE id=?", (aid,)).fetchone()
        assert a["status"] == "ended" and a["winner_id"] == u1, "vítěz = poslední vedoucí"
        assert _pts(conn, u1) == 100000 - 550, "vítězovy sedláci (i poplatek) zůstaly odečtené (sink)"
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
        assert _pts(conn, u1) == 100000 - 330
        r = auctions.cancel(conn, aid)
        assert r["ok"] and r["refunded"] == 300
        assert _pts(conn, u1) == 100000, "zrušení vrátilo vůdci escrow i vstupní poplatek"
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
        auctions.bid(conn, _row(conn, u1), aid, 200)            # u1 vede na 200 (+20 poplatek)
        assert _pts(conn, u1) == 100000 - 220
        r = auctions.buy_now(conn, _row(conn, u2), aid)         # u2 vykoupí
        assert r["ok"] and r["price"] == 5000
        assert _pts(conn, u2) == 100000 - 5000
        assert _pts(conn, u1) == 100000 - 20, "vykoupený vůdce dostal escrow 100 % zpět (poplatek zůstává)"
        a = conn.execute("SELECT status, winner_id FROM auctions WHERE id=?", (aid,)).fetchone()
        assert a["status"] == "ended" and a["winner_id"] == u2
        assert not auctions.buy_now(conn, _row(conn, _user(conn)), aid).get("ok"), "po skončení už ne"
    finally:
        conn.close()


def test_community_market_payout_and_own_listing_gate():
    """Komisní skin drží cenu v escrow a vyplatí 95 % právě jednou až po převzetí."""
    from app.db import get_conn
    from app import auctions
    conn = get_conn()
    try:
        seller, buyer, bidder = _user(conn), _user(conn), _user(conn)
        seller_name = _row(conn, seller)["username"]
        conn.commit()

        made = auctions.create(conn, "Community skin", "", 100, 50, 10,
                               buy_now=10000, seller_username=seller_name)
        assert made["ok"] and made["seller"] == seller_name
        aid = made["id"]
        assert not auctions.bid(conn, _row(conn, seller), aid, 100).get("ok")
        assert not auctions.buy_now(conn, _row(conn, seller), aid).get("ok")
        assert _pts(conn, seller) == 100000

        sold = auctions.buy_now(conn, _row(conn, buyer), aid)
        assert sold["ok"] and sold["market_escrow"] is True
        assert _pts(conn, buyer) == 90000 and _pts(conn, seller) == 100000
        row = conn.execute("SELECT seller_payout,market_fee,seller_paid_at,delivery_status FROM auctions WHERE id=?", (aid,)).fetchone()
        assert row["seller_payout"] == 0 and row["seller_paid_at"] is None
        assert row["delivery_status"] == "awaiting_delivery"
        assert not auctions.confirm_delivery(conn, _row(conn, buyer), aid)["ok"]
        assert auctions.mark_delivered(conn, _row(conn, seller), aid)["ok"]
        settled = auctions.confirm_delivery(conn, _row(conn, buyer), aid)
        assert settled["ok"] and settled["seller_payout"] == 9500 and settled["market_fee"] == 500
        assert _pts(conn, seller) == 109500
        auctions.list_public(conn)
        assert _pts(conn, seller) == 109500, "opakované načtení nesmí vyplatit prodej podruhé"

        aid2 = auctions.create(conn, "Timed community skin", "", 1000, 50, 10,
                               seller_username=seller_name)["id"]
        assert auctions.bid(conn, _row(conn, bidder), aid2, 1000)["ok"]
        conn.execute("UPDATE auctions SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?", (aid2,))
        conn.commit()
        auctions.list_public(conn)
        assert _pts(conn, seller) == 109500, "časový konec jen otevře escrow"
        assert auctions.mark_delivered(conn, _row(conn, seller), aid2)["ok"]
        assert auctions.confirm_delivery(conn, _row(conn, bidder), aid2)["seller_payout"] == 950
        assert _pts(conn, seller) == 110450
        assert not auctions.create(conn, "Bad seller", "", 100, 50, 10,
                                   seller_username="missing-user")["ok"]
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
        auctions.bid(conn, _row(conn, u1), aid, 200)        # u1 vede 200 (+20 poplatek)
        auctions.bid(conn, _row(conn, u2), aid, 400)        # u2 přehodí (+40 poplatek) → u1 dostane 100 % (200)
        assert _pts(conn, u1) == 100000 - 20
        assert _pts(conn, u2) == 100000 - 440
        r = auctions.buy_now(conn, _row(conn, u3), aid)     # u3 vykoupí
        assert r["ok"] and _pts(conn, u3) == 100000 - 50000
        assert _pts(conn, u2) == 100000 - 40, "aktuální vůdce u2 dostal escrow 100 % zpět (poplatek zůstává)"
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
        auctions.bid(conn, _row(conn, u1), aid, 900)        # (+90 poplatek, u2 se netýká)
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
        auctions.bid(conn, _row(conn, u1), aid, 300)        # +30 poplatek
        auctions.bid(conn, _row(conn, u2), aid, 600)        # u2 vede (+60 poplatek), u1 dostal 100 % (300)
        r = auctions.cancel(conn, aid)
        assert r["ok"] and r["refunded"] == 600
        assert _pts(conn, u2) == 100000, "aktuální vůdce u2 dostal escrow i poplatek zpět"
        assert _pts(conn, u1) == 100000, "u1 dostal při zrušení zpět i vstupní poplatek"
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
