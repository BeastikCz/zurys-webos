"""Statek (mini-farma): loop + levely + krmivo + prodej + sbírka + sub-only + utility kůň.

    .venv/Scripts/python.exe -m pytest tests/test_farm.py -v
"""
import secrets


def _user(conn, is_sub=0, points=300000):
    from app.db import now_iso
    u = f"farm_{secrets.token_hex(3)}"
    return conn.execute(
        "INSERT INTO users (kick_username, username, role, points, earned_total, is_sub, created_at) "
        "VALUES (?,?,?,?,0,?,?)", (u, u, "user", points, is_sub, now_iso())).lastrowid


def _row(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _ready_now(conn, uid, slot):
    conn.execute("UPDATE farm_animals SET ready_at='2000-01-01T00:00:00+00:00' WHERE user_id=? AND slot=?", (uid, slot))
    conn.commit()


def test_full_loop_and_xp():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        assert farm.buy(conn, _row(conn, uid), "chicken")["ok"]
        initial = farm.status(conn, _row(conn, uid))
        assert initial["slots"][0]["state"] == "hungry"
        assert "live" not in initial, "Statek už nemá LIVE ×2 produkci"
        fed = farm.feed(conn, _row(conn, uid), 0)
        assert fed["ok"] and "live_boost" not in fed
        assert not farm.collect(conn, _row(conn, uid), 0).get("ok")     # moc brzy
        _ready_now(conn, uid, 0)
        et0 = _row(conn, uid)["earned_total"]
        rc = farm.collect(conn, _row(conn, uid), 0)
        assert rc["ok"] and rc["reward"] >= 130
        assert _row(conn, uid)["earned_total"] > et0, "produkt dal XP (garden bucket)"
        assert farm.status(conn, _row(conn, uid))["slots"][0]["state"] == "hungry"   # zase hlad
        assert not farm.collect(conn, _row(conn, uid), 0).get("ok")     # anti-double
    finally:
        conn.close()


def test_levels_scale_reward():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        farm.buy(conn, _row(conn, uid), "chicken")
        # nakrm FEED_PER_LEVEL× (mezi tím sbírej) → level 2
        leveled = False
        for _ in range(farm.FEED_PER_LEVEL):
            rf = farm.feed(conn, _row(conn, uid), 0)
            leveled = leveled or rf.get("leveled_up")
            _ready_now(conn, uid, 0)
            farm.collect(conn, _row(conn, uid), 0)
        assert leveled, "po FEED_PER_LEVEL krmení musí přijít level up"
        st = farm.status(conn, _row(conn, uid))["slots"][0]
        assert st["level"] >= 2 and st["reward"] > 130, "level zvedl výnos"
    finally:
        conn.close()


def test_krmivo_from_harvest_then_feed():
    from app.db import get_conn
    from app import farm, garden
    from datetime import datetime, timezone, timedelta
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        user = _row(conn, uid)
        # zasaď + přetoč + sklidí → padne krmivo
        garden.plant(conn, user, 0, "mrkev")
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0, pest_at=NULL WHERE user_id=? AND plot=0", (uid,))
        conn.commit()
        garden.harvest(conn, _row(conn, uid), 0)
        assert _row(conn, uid)["feed_stock"] >= 1, "sklizeň shodila krmivo"
        # krmení zvířete použije krmivo (zdarma, bez sedláků)
        farm.buy(conn, _row(conn, uid), "chicken")
        bal = _row(conn, uid)["points"]; stock = _row(conn, uid)["feed_stock"]
        rf = farm.feed(conn, _row(conn, uid), 0)
        assert rf["used_krmivo"] is True
        assert _row(conn, uid)["points"] == bal, "krmivem zdarma → sedláci netknuté"
        assert _row(conn, uid)["feed_stock"] == stock - 1
    finally:
        conn.close()


def test_sell_refund_and_collection_persists():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        farm.buy(conn, _row(conn, uid), "chicken")
        bal = _row(conn, uid)["points"]
        rs = farm.sell(conn, _row(conn, uid), 0)
        assert rs["ok"] and rs["refund"] == 1000, rs            # 50 % z 2000
        assert _row(conn, uid)["points"] == bal + 1000
        assert farm.status(conn, _row(conn, uid))["slots"][0]["empty"], "slot uvolněn"
        coll = farm.status(conn, _row(conn, uid))["collection"]
        assert coll["have"] >= 1, "sbírka po prodeji zůstává"
    finally:
        conn.close()


def test_collection_complete_reward():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        all_keys = [a["key"] for a in farm.ANIMALS]
        bal0 = _row(conn, uid)["points"]
        for k in all_keys:                                       # zapiš všechny druhy do sbírky
            farm._note_collection(conn, uid, k)
        conn.commit()
        st = farm.status(conn, _row(conn, uid))
        assert st["collection"]["complete"] is True
        assert _row(conn, uid)["points"] == bal0 + farm.COLLECTION_REWARD, "kompletní sbírka = bonus 1×"
        # podruhé už nic
        farm._note_collection(conn, uid, all_keys[0]); conn.commit()
        assert _row(conn, uid)["points"] == bal0 + farm.COLLECTION_REWARD
    finally:
        conn.close()


def test_contract_progress_and_claim():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        farm.buy(conn, _row(conn, uid), "chicken")
        farm.status(conn, _row(conn, uid))                       # založí dnešní zakázku
        con = farm.status(conn, _row(conn, uid))["contract"]
        assert con and con["items"][0]["key"] == "chicken" and not con["done"]
        assert not farm.claim_contract(conn, _row(conn, uid)).get("ok"), "nesplněná nejde vyzvednout"
        goal = con["items"][0]["goal"]
        for _ in range(goal):                                    # nasbírej potřebné produkty
            farm.feed(conn, _row(conn, uid), 0)
            _ready_now(conn, uid, 0)
            farm.collect(conn, _row(conn, uid), 0)
        con = farm.status(conn, _row(conn, uid))["contract"]
        assert con["done"] and con["items"][0]["have"] == goal
        bal = _row(conn, uid)["points"]
        rc = farm.claim_contract(conn, _row(conn, uid))
        assert rc["ok"] and _row(conn, uid)["points"] == bal + con["reward"]
        assert not farm.claim_contract(conn, _row(conn, uid)).get("ok"), "podruhé už ne"
    finally:
        conn.close()


def test_barn_upgrade_adds_slot():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        assert farm.status(conn, _row(conn, uid))["n_slots"] == farm.BASE_SLOTS
        bal = _row(conn, uid)["points"]
        r = farm.upgrade_barn(conn, _row(conn, uid))
        assert r["ok"] and r["level"] == 2
        assert _row(conn, uid)["points"] == bal - farm.BARN_COSTS[2]
        assert farm.status(conn, _row(conn, uid))["n_slots"] == farm.BASE_SLOTS + 1, "stodola dala +1 slot"
        # nedostatek sedláků na další upgrade → error, nic se nestrhne
        conn.execute("UPDATE users SET points = 10 WHERE id = ?", (uid,)); conn.commit()
        assert not farm.upgrade_barn(conn, _row(conn, uid)).get("ok")
        assert _row(conn, uid)["points"] == 10
    finally:
        conn.close()


def test_patron_gifts_unlock_slot_and_fox_guard():
    from app.db import get_conn, now_iso
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        assert farm._n_slots(conn, _row(conn, uid)) == farm.BASE_SLOTS
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 0, "Kick gift sub 🎁 ×5", now_iso()))
        conn.commit()
        patron = farm._patron_status(conn, uid)
        assert patron["title"] == "Patron statku" and patron["slot_bonus"]
        assert farm._n_slots(conn, _row(conn, uid)) == farm.BASE_SLOTS + 1
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 0, "Kick gift sub 🎁 ×10", now_iso()))
        conn.commit()
        patron = farm._patron_status(conn, uid)
        assert patron["gifts"] == 15 and patron["fox_guard"]
        old_uid = _user(conn)
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (old_uid, 0, "Kick gift sub 🎁 ×5", "2020-01-01T00:00:00+00:00"))
        conn.commit()
        old_patron = farm._patron_status(conn, old_uid)
        assert old_patron["gifts"] == 0 and not old_patron["slot_bonus"], "patron slot končí se sezónou"
    finally:
        conn.close()


def test_gift_turbo_is_single_use_and_capped():
    from datetime import datetime, timezone
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        farm.buy(conn, _row(conn, uid), "chicken")
        assert farm.grant_turbo_tokens(conn, uid, 1) == 1
        assert farm.status(conn, _row(conn, uid))["turbo"]["count"] == 1
        r = farm.feed(conn, _row(conn, uid), 0, turbo=True)
        assert r["ok"] and r["turbo"] and r["turbo_left"] == 0
        ready = conn.execute("SELECT ready_at FROM farm_animals WHERE user_id=? AND slot=0", (uid,)).fetchone()["ready_at"]
        assert 3400 < (datetime.fromisoformat(ready) - datetime.now(timezone.utc)).total_seconds() < 3700
        assert farm.grant_turbo_tokens(conn, uid, 4) == farm.TURBO_MAX_STORED
    finally:
        conn.close()


def test_confirmed_gift_event_grants_turbo_tokens():
    from app.db import get_conn
    from app import farm, kickevents
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        gifter = _row(conn, uid)["kick_username"]
        r = kickevents.handle_event(conn, "channel.subscription.gifts", {
            "gifter": {"username": gifter},
            "giftees": [{"username": "turbo_giftee_a"}, {"username": "turbo_giftee_b"}],
        })
        conn.commit()
        assert r["ok"] and r["count"] == 2
        assert farm.status(conn, _row(conn, uid))["turbo"]["count"] == 2
    finally:
        conn.close()


def test_sub_only_unicorn():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        free = _user(conn, is_sub=0); sub = _user(conn, is_sub=1); conn.commit()
        assert not farm.buy(conn, _row(conn, free), "unicorn").get("ok"), "non-sub nesmí jednorožce"
        assert farm.buy(conn, _row(conn, sub), "unicorn")["ok"], "sub smí jednorožce"
    finally:
        conn.close()


def test_utility_horse_bonus():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn, is_sub=1); conn.commit()             # sub = 3 sloty
        farm.buy(conn, _row(conn, uid), "horse")                # utility
        farm.buy(conn, _row(conn, uid), "chicken")
        # kůň nejde krmit/sbírat
        hslot = next(s["slot"] for s in farm.status(conn, _row(conn, uid))["slots"] if s.get("utility"))
        assert not farm.feed(conn, _row(conn, uid), hslot).get("ok")
        # produkce slepičky je o +10 % (kůň bonus)
        cslot = next(s["slot"] for s in farm.status(conn, _row(conn, uid))["slots"] if not s.get("empty") and not s.get("utility"))
        st = farm.status(conn, _row(conn, uid))["slots"][cslot]
        assert st["reward"] == round(130 * 1.10), f"kůň +10 % → {st['reward']}"
    finally:
        conn.close()


def test_starter_chicken_discount_once():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn, is_sub=1); conn.commit()             # sub = 3 sloty (ať se vejdou 2 slepice)
        assert farm.status(conn, _row(conn, uid))["starter"] is True
        cat = {a["key"]: a for a in farm._animals_public(conn, _row(conn, uid))}
        assert cat["chicken"]["cost"] == farm.STARTER_COST and cat["chicken"]["starter"]
        assert cat["goat"]["cost"] == 6000, "sleva jen na slepici"
        bal = _row(conn, uid)["points"]
        assert farm.buy(conn, _row(conn, uid), "chicken")["ok"]
        assert _row(conn, uid)["points"] == bal - farm.STARTER_COST, "první slepice za startovací cenu"
        # druhá už za plnou
        bal = _row(conn, uid)["points"]
        assert farm.buy(conn, _row(conn, uid), "chicken")["ok"]
        assert _row(conn, uid)["points"] == bal - 2000
        assert farm.status(conn, _row(conn, uid))["starter"] is False
    finally:
        conn.close()


def test_fox_ransom_scales_with_product():
    import json
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        farm.buy(conn, _row(conn, uid), "chicken")
        farm.feed(conn, _row(conn, uid), 0)
        _ready_now(conn, uid, 0)
        # nasimuluj lišku přesně jak ji staví _roll_fox (s ransom z hodnoty produktu)
        r = conn.execute("SELECT ready_at, fed_count FROM farm_animals WHERE user_id=? AND slot=0", (uid,)).fetchone()
        value = farm._reward_at(farm._BY_KEY["chicken"], farm._level(r["fed_count"]), 0)
        expect = max(farm.FOX_RANSOM_MIN, min(farm.FOX_RANSOM_MAX, int(value * farm.FOX_RANSOM_PCT)))
        assert expect < 130, "výkupné za vejce (130) musí být míň než produkt – jinak se nevyplatí platit"
        conn.execute("UPDATE users SET farm_fox=? WHERE id=?",
                     (json.dumps({"slot": 0, "ready_at": r["ready_at"], "ransom": expect}), uid))
        conn.commit()
        assert farm.status(conn, _row(conn, uid))["fox"]["ransom"] == expect
        bal = _row(conn, uid)["points"]
        rf = farm.resolve_fox(conn, _row(conn, uid), pay=True)
        assert rf["ok"] and rf["ransom"] == expect
        assert _row(conn, uid)["points"] == bal - expect
        assert farm.collect(conn, _row(conn, uid), 0)["ok"], "po zaplacení jde produkt sebrat"
    finally:
        conn.close()


def test_daily_soft_cap_reduces_payout():
    from app.db import get_conn, now_iso
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        farm.buy(conn, _row(conn, uid), "chicken")
        # pod stropem: plný výnos
        farm.feed(conn, _row(conn, uid), 0); _ready_now(conn, uid, 0)
        rc = farm.collect(conn, _row(conn, uid), 0)
        assert rc["ok"] and rc["reward"] >= 130
        assert farm._farm_today(conn, uid) >= 130
        # nad stropem: jen FARM_SOFT_RATE
        conn.execute("UPDATE users SET farm_today=?, farm_day=? WHERE id=?",
                     (farm.FARM_DAILY_FULL, now_iso()[:10], uid))
        conn.commit()
        farm.feed(conn, _row(conn, uid), 0); _ready_now(conn, uid, 0)
        rc = farm.collect(conn, _row(conn, uid), 0)
        assert rc["ok"] and rc["reward"] <= int(130 * 1.1 * farm.FARM_SOFT_RATE) * farm.GOLDEN_MULT
        assert rc["reward"] < 130 or rc["golden"], f"nad strop musí být snížený výnos: {rc}"
        # jiný den = reset
        conn.execute("UPDATE users SET farm_day='2000-01-01' WHERE id=?", (uid,)); conn.commit()
        assert farm._farm_today(conn, uid) == 0
    finally:
        conn.close()


def test_golden_event_boosts_chance():
    from app.db import get_conn
    from app import farm
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        assert farm._golden_chance(conn) == farm.GOLDEN_CHANCE
        r = farm.golden_event_start(conn, 7)
        assert r["until"] > "2026" and farm._golden_chance(conn) == farm.GOLDEN_EVENT_CHANCE
        st = farm.status(conn, _row(conn, uid))
        assert st["golden_event"] is True and st["golden_pct"] == int(farm.GOLDEN_EVENT_CHANCE * 100)
        farm.golden_event_stop(conn)
        assert farm._golden_chance(conn) == farm.GOLDEN_CHANCE
        assert farm.status(conn, _row(conn, uid))["golden_event"] is False
    finally:
        conn.close()


def test_farm_public_gate():
    from fastapi import HTTPException
    from app.db import get_conn, set_setting
    from app.deps import require_farm_access
    conn = get_conn()
    try:
        uid = _user(conn); conn.commit()
        set_setting(conn, "farm_public", "0"); conn.commit()
        try:
            require_farm_access(_row(conn, uid), conn)
            assert False, "před launchem musí být 403"
        except HTTPException as e:
            assert e.status_code == 403
        set_setting(conn, "farm_public", "1"); conn.commit()
        assert require_farm_access(_row(conn, uid), conn)["id"] == uid, "po launchi projde běžný user"
        set_setting(conn, "farm_public", "0"); conn.commit()
    finally:
        conn.close()
