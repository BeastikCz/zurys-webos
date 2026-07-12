"""Regrese na souběhové (TOCTOU) bugy: násobné výplaty / přeprodej skladu přes paralelní requesty.

Testy jsou DETERMINISTICKÉ – nesimulují vlákna, ale odehrají „prohraný závod" ručně: druhý aktér
dostane stejný starý (stale) stav, jaký by viděl při souběhu, a ověří se, že atomická pojistka
(podmíněný UPDATE s rowcount / INSERT OR IGNORE) ho odmítne a odměna se připíše právě jednou.

    .venv/Scripts/python.exe -m pytest tests/test_race_fixes.py -v
"""
import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException


def _mk(conn, points=100_000, **extra):
    from app.db import now_iso
    u = f"rc_{secrets.token_hex(3)}"
    cols = "kick_username, username, role, points, created_at"
    vals = [u, u, "user", points, now_iso()]
    for k, v in extra.items():
        cols += f", {k}"
        vals.append(v)
    uid = conn.execute(
        f"INSERT INTO users ({cols}) VALUES ({','.join('?' * len(vals))})", vals).lastrowid
    conn.commit()
    return uid


def _points(conn, uid):
    return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]


def _row(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


# ---------------- Denní bonus: 16 requestů → 1 odměna ----------------
def test_daily_claim_no_double_pay(client):
    from app.db import get_conn
    from app.routers.misc import daily_claim
    conn = get_conn()
    try:
        uid = _mk(conn, points=0)
        stale = _row(conn, uid)                       # snapshot: last_daily=NULL (jako u všech souběžných req.)
        r1 = daily_claim(user=stale, conn=conn)
        assert r1["ok"] and r1["reward"] > 0
        after = _points(conn, uid)
        assert after == r1["reward"]
        # „prohraný" souběžný request: stejný starý řádek (last_daily stále NULL), ale DB už přepsaná
        with pytest.raises(HTTPException) as ex:
            daily_claim(user=stale, conn=conn)
        assert ex.value.status_code == 400
        assert _points(conn, uid) == after           # žádná druhá výplata
        n = conn.execute("SELECT COUNT(*) c FROM points_log WHERE user_id=? AND reason LIKE 'Snídaně na statku%'",
                         (uid,)).fetchone()["c"]
        assert n == 1
    finally:
        conn.close()


# ---------------- PvP cancel: 1 hra → 1 refund ----------------
def test_cancel_game_refund_once(client):
    from app.db import get_conn, now_iso
    from app.routers.games import cancel_game
    conn = get_conn()
    try:
        uid = _mk(conn, points=0)
        gid = conn.execute(
            "INSERT INTO games (type, status, stake, board, turn, p1_id, created_at, updated_at) "
            "VALUES ('gomoku','open',?,?,1,?,?,?)",
            (500, "." * 144, uid, now_iso(), now_iso())).lastrowid
        conn.commit()
        user = _row(conn, uid)
        assert cancel_game(gid=gid, user=user, conn=conn)["ok"] is True
        assert _points(conn, uid) == 500             # vklad vrácen
        with pytest.raises(HTTPException) as ex:
            cancel_game(gid=gid, user=user, conn=conn)
        assert ex.value.status_code == 400
        assert _points(conn, uid) == 500             # ne 1000
    finally:
        conn.close()


# ---------------- Timeout / dohrání: banka se vyplatí jednou ----------------
def test_finish_pays_bank_once(client):
    from app.db import get_conn, now_iso
    from app.routers.games import _finish
    conn = get_conn()
    try:
        p1 = _mk(conn, points=0)
        p2 = _mk(conn, points=0)
        gid = conn.execute(
            "INSERT INTO games (type, status, stake, board, turn, p1_id, p2_id, active_at, last_move_at, created_at, updated_at) "
            "VALUES ('gomoku','active',?,?,2,?,?,?,?,?,?)",
            (200, "." * 144, p1, p2, now_iso(), now_iso(), now_iso(), now_iso())).lastrowid
        conn.commit()
        g = conn.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()  # stale 'active' snapshot
        assert _finish(conn, g, 1) is True           # p1 vyhrál timeoutem
        conn.commit()
        won = _points(conn, p1)
        assert won > 0
        # druhý „souběžný" claim se stejným starým řádkem → atomicky odmítnut
        assert _finish(conn, g, 1) is False
        conn.commit()
        assert _points(conn, p1) == won              # banka vyplacena jen jednou
    finally:
        conn.close()


# ---------------- Shop: duplicitní položka neobejde sklad ----------------
def test_cart_duplicate_item_respects_stock(client):
    from app.db import get_conn, now_iso
    from app.services import validate_items
    conn = get_conn()
    try:
        uid = _mk(conn)
        pid = conn.execute(
            "INSERT INTO products (name, cost_points, type, stock, active, created_at) VALUES (?,?,?,?,1,?)",
            ("RaceItem " + secrets.token_hex(2), 100, "instant", 1, now_iso())).lastrowid
        conn.commit()
        user = _row(conn, uid)
        # jeden kus projde
        total, err = validate_items(conn, user, [(pid, 1)])
        assert err is None and total == 100
        # stejná položka 2× v košíku se sloučí na qty=2 → přes sklad 1 NEprojde
        total, err = validate_items(conn, user, [(pid, 1), (pid, 1)])
        assert err is not None and "skladem" in err
    finally:
        conn.close()


# ---------------- Shop: souběžné nákupy posledního kusu nepřeprodají sklad ----------------
def test_apply_purchase_no_oversell(client):
    from app.db import get_conn, now_iso
    from app.services import apply_purchase
    conn = get_conn()
    try:
        a = _mk(conn)
        b = _mk(conn)
        pid = conn.execute(
            "INSERT INTO products (name, cost_points, type, stock, active, created_at) VALUES (?,?,?,?,1,?)",
            ("LastOne " + secrets.token_hex(2), 100, "instant", 1, now_iso())).lastrowid
        conn.commit()
        ids = apply_purchase(conn, _row(conn, a), [(pid, 1)])
        assert len(ids) == 1
        assert conn.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()["stock"] == 0
        # druhý nákup posledního kusu → vyprodáno (atomický odpočet rowcount==0)
        with pytest.raises(HTTPException) as ex:
            apply_purchase(conn, _row(conn, b), [(pid, 1)])
        assert ex.value.status_code == 400
        assert conn.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()["stock"] == 0  # ne -1
    finally:
        conn.close()


# ---------------- Predikce: souběžné staff vyhodnocení vyplatí bank jen jednou ----------------
def _staff_token(conn):
    from app.db import now_iso
    suf = secrets.token_hex(4)
    uid = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
        (f"adm_{suf}", f"adm_{suf}", "admin", now_iso())).lastrowid
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit()
    return token


def _setup_pred(conn):
    from app.db import now_iso
    b1 = _mk(conn, points=1000)
    b2 = _mk(conn, points=1000)
    pid = conn.execute("INSERT INTO predictions (question, game, status, created_at) VALUES ('Q','x','open',?)",
                       (now_iso(),)).lastrowid
    optA = conn.execute("INSERT INTO prediction_options (prediction_id, label, position) VALUES (?,?,0)",
                        (pid, "A")).lastrowid
    optB = conn.execute("INSERT INTO prediction_options (prediction_id, label, position) VALUES (?,?,1)",
                        (pid, "B")).lastrowid
    conn.execute("INSERT INTO prediction_bets (prediction_id, option_id, user_id, amount, payout, created_at) "
                 "VALUES (?,?,?,100,0,?)", (pid, optA, b1, now_iso()))
    conn.execute("INSERT INTO prediction_bets (prediction_id, option_id, user_id, amount, payout, created_at) "
                 "VALUES (?,?,?,100,0,?)", (pid, optB, b2, now_iso()))
    conn.commit()
    return b1, b2, pid, optA, optB


def test_resolve_concurrent_pays_bank_once(client):
    """6 souběžných /resolve na stejnou predikci → vítěz dostane bank právě jednou (ne N×)."""
    from app.db import get_conn
    from app.config import SESSION_COOKIE
    conn = get_conn()
    try:
        b1, b2, pid, optA, optB = _setup_pred(conn)
        token = _staff_token(conn)
    finally:
        conn.close()

    barrier = threading.Barrier(6)
    codes = []

    def go():
        barrier.wait()      # ať odstartují co nejvíc naráz (max šance trefit závod)
        r = client.post(f"/api/predictions/{pid}/resolve", json={"option_id": optA},
                        headers={"Cookie": f"{SESSION_COOKIE}={token}"})
        codes.append(r.status_code)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # b1 (vítěz, vsadil 100, bank 200) musí dostat přesně +200 navrch – ne dvojnásobek
    assert _points(conn_b1 := get_conn(), b1) == 1000 + 200, "vítěz vyplacen víc než jednou!"
    conn_b1.close()
    assert codes.count(200) == 1, f"resolve smí uspět jen jednou, kódy={codes}"


def test_cancel_concurrent_refunds_once(client):
    """6 souběžných /cancel → každý sázející dostane vklad zpět právě jednou."""
    from app.db import get_conn
    from app.config import SESSION_COOKIE
    conn = get_conn()
    try:
        b1, b2, pid, optA, optB = _setup_pred(conn)
        token = _staff_token(conn)
    finally:
        conn.close()

    barrier = threading.Barrier(6)

    def go():
        barrier.wait()
        client.post(f"/api/predictions/{pid}/cancel", headers={"Cookie": f"{SESSION_COOKIE}={token}"})

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    c = get_conn()
    try:
        assert _points(c, b1) == 1000 + 100, "vklad b1 vrácen víc než jednou!"
        assert _points(c, b2) == 1000 + 100, "vklad b2 vrácen víc než jednou!"
        assert c.execute("SELECT status FROM predictions WHERE id=?", (pid,)).fetchone()["status"] == "cancelled"
    finally:
        c.close()


# ---------------- Snídaně na statku: sub ×3 + truhla den 7 ----------------
def test_daily_breakfast_sub_and_chest(client):
    from app.db import get_conn
    from app.routers.misc import daily_claim, DAILY_LADDER, DAILY_SUB_MULT, DAILY_CHEST_SUB
    conn = get_conn()
    try:
        uid = _mk(conn, points=0, is_sub=1, daily_streak=6)   # den 7 (idx 6), sub
        r = daily_claim(user=_row(conn, uid), conn=conn)
        base = DAILY_LADDER[6] * DAILY_SUB_MULT
        assert r["sub"] and r["day"] == 7
        assert base <= r["reward"] <= base + DAILY_CHEST_SUB
        assert r["reward"] == base + r["chest"]
        assert _points(conn, uid) == r["reward"]

        uid2 = _mk(conn, points=0, daily_streak=2)            # den 3, free: bez multu, bez truhly
        r2 = daily_claim(user=_row(conn, uid2), conn=conn)
        assert not r2["sub"] and r2["chest"] == 0 and r2["reward"] == DAILY_LADDER[2]
    finally:
        conn.close()


# ---------------- Kolo štěstí v2: sub 2 spiny + placený re-spin ----------------
def test_wheel_sub_spins_and_paid_respin(client):
    from app.db import get_conn
    from app.routers.misc import wheel_spin, WHEEL_RESPIN_COST
    from app.models import WheelSpinIn
    conn = get_conn()
    try:
        uid = _mk(conn, points=10_000, is_sub=1)
        r1 = wheel_spin(data=None, user=_row(conn, uid), conn=conn)
        assert r1["ok"] and r1["spins_left"] == 1            # sub: zbývá 2. free spin
        r2 = wheel_spin(data=None, user=_row(conn, uid), conn=conn)
        assert r2["ok"] and r2["spins_left"] == 0 and r2["respin_available"]
        with pytest.raises(HTTPException):                   # 3. free spin nejde
            wheel_spin(data=None, user=_row(conn, uid), conn=conn)
        before = _points(conn, uid)
        r3 = wheel_spin(data=WheelSpinIn(paid=True), user=_row(conn, uid), conn=conn)
        assert r3["ok"] and not r3["respin_available"]
        assert _points(conn, uid) == before - WHEEL_RESPIN_COST + r3["amount"]
        with pytest.raises(HTTPException):                   # 2. re-spin nejde
            wheel_spin(data=WheelSpinIn(paid=True), user=_row(conn, uid), conn=conn)

        uid2 = _mk(conn, points=0)                           # free hráč: 1 spin, re-spin bez sedláků neprojde
        r = wheel_spin(data=None, user=_row(conn, uid2), conn=conn)
        assert r["spins_left"] == 0 and r["respin_available"]
        with pytest.raises(HTTPException) as ex:
            wheel_spin(data=WheelSpinIn(paid=True), user=_row(conn, uid2), conn=conn)
        assert "potřebuješ" in ex.value.detail
        assert _points(conn, uid2) == r["amount"]            # nic se nestrhlo
    finally:
        conn.close()
