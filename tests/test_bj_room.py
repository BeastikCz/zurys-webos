"""Soukromý sdílený blackjack stůl (multiplayer + chat): pokoje, sázení, rozdání,
vyhodnocení (3:2 / win / push / bust), AFK auto-stand, idempotence výplaty, chat, odchod.
Vyhodnocení testováno deterministicky přes přímý insert konkrétních karet.

    .venv/Scripts/python.exe -m pytest tests/test_bj_room.py -v
"""
import json
import secrets
from datetime import datetime, timezone, timedelta

from app.db import get_conn, now_iso
from app import bj_room


def _mk_user(points=1000):
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"bjr_{suf}", f"bjr_{suf}", "user", points, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _points(uid):
    conn = get_conn()
    try:
        return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()


def _run(fn, *a):
    conn = get_conn()
    try:
        return fn(conn, *a)
    finally:
        conn.close()


def _mk_room_playing(host_id, dealer, deck, deck_pos):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO bj_rooms (code, host_id, status, dealer, deck, deck_pos, round_no, created_at, updated_at) "
            "VALUES (?,?,'playing',?,?,?,1,?,?)",
            ("BJ" + secrets.token_hex(3).upper(), host_id, json.dumps(dealer), json.dumps(deck), deck_pos,
             now_iso(), now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _add_seat(room_id, uid, bet, hand, state, acted_at=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO bj_seats (room_id, user_id, bet, hand, state, acted_at, joined_at, seen_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (room_id, uid, bet, json.dumps(hand), state, acted_at, now_iso(), now_iso()))
        conn.commit()
    finally:
        conn.close()


def _seatrow(room_id, uid):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM bj_seats WHERE room_id=? AND user_id=?", (room_id, uid)).fetchone()
    finally:
        conn.close()


def _roomrow(room_id):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM bj_rooms WHERE id=?", (room_id,)).fetchone()
    finally:
        conn.close()


# ---- pokoj / posezení ----
def test_create_and_join(client):
    h = _mk_user(); st = _run(bj_room.create, h, "host")
    assert st["code"].startswith("BJ") and st["is_host"] and len(st["seats"]) == 1
    p2 = _mk_user(); st2 = _run(bj_room.join, p2, "p2", st["code"])
    assert len(st2["seats"]) == 2


def test_full_table_rejected(client):
    h = _mk_user(); st = _run(bj_room.create, h, "host")
    for _ in range(5):
        _run(bj_room.join, _mk_user(), "x", st["code"])     # +5 = 6 celkem (plno)
    try:
        _run(bj_room.join, _mk_user(), "x", st["code"])
        assert False, "7. hráč nesmí"
    except ValueError as e:
        assert "pln" in str(e).lower()


def test_bet_debits_and_locks(client):
    h = _mk_user(1000); st = _run(bj_room.create, h, "host")
    rid = st["room_id"]
    _run(bj_room.place_bet, h, rid, 100)
    assert _points(h) == 900 and _seatrow(rid, h)["state"] == "ready"
    try:
        _run(bj_room.place_bet, h, rid, 50)
        assert False, "druhá sázka v jednom kole nesmí"
    except ValueError:
        pass


def test_only_host_deals(client):
    h = _mk_user(); st = _run(bj_room.create, h, "host")
    rid = st["room_id"]
    p2 = _mk_user(1000); _run(bj_room.join, p2, "p2", st["code"])
    _run(bj_room.place_bet, p2, rid, 100)
    try:
        _run(bj_room.deal, p2, rid)
        assert False, "ne-host nesmí rozdat"
    except ValueError as e:
        assert "host" in str(e).lower()
    st2 = _run(bj_room.deal, h, rid)
    assert st2["status"] in ("playing", "done")


# ---- vyhodnocení (deterministicky) ----
def test_resolve_payouts(client):
    h = _mk_user()
    win, bj, lose = _mk_user(), _mk_user(), _mk_user()
    rid = _mk_room_playing(h, ["TS", "6D"], ["2C"], 0)     # dealer 16 → dobere 2 → 18
    _add_seat(rid, win, 100, ["KS", "9D"], "stood")        # 19 → výhra
    _add_seat(rid, bj, 100, ["AS", "KD"], "stood")         # blackjack → 3:2
    _add_seat(rid, lose, 100, ["TS", "7D"], "stood")       # 17 < 18 → prohra
    bw, bb, bl = _points(win), _points(bj), _points(lose)
    _run(bj_room._maybe_resolve, rid)
    assert _roomrow(rid)["status"] == "done"
    assert _points(win) == bw + 200
    assert _points(bj) == bb + 250
    assert _points(lose) == bl
    assert _seatrow(rid, win)["result"] == "win"
    assert _seatrow(rid, bj)["result"] == "blackjack"


def test_resolve_idempotent(client):
    h = _mk_user(); win = _mk_user()
    rid = _mk_room_playing(h, ["TS", "9C"], [], 0)         # dealer 19, nedobírá
    _add_seat(rid, win, 100, ["KS", "KD"], "stood")        # 20 → výhra
    b = _points(win)
    _run(bj_room._maybe_resolve, rid)
    _run(bj_room._maybe_resolve, rid)                       # 2× nesmí vyplatit 2×
    assert _points(win) == b + 200


def test_afk_auto_stand_then_resolve(client):
    h = _mk_user(); p = _mk_user()
    rid = _mk_room_playing(h, ["TS", "9C"], [], 0)         # dealer 19
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    _add_seat(rid, p, 100, ["KS", "5D"], "acting", acted_at=old)   # 15, AFK 120 s
    b = _points(p)
    _run(bj_room._maybe_resolve, rid)
    assert _seatrow(rid, p)["state"] == "resolved"
    assert _roomrow(rid)["status"] == "done"
    assert _points(p) == b                                  # 15 < 19 → prohra


# ---- chat / odchod ----
def test_chat_send_and_show(client):
    h = _mk_user(); st = _run(bj_room.create, h, "host")
    rid = st["room_id"]
    _run(bj_room.chat_send, h, "host", rid, "ahoj u stolu")
    s = _run(bj_room.state, h, rid)
    assert any(m["msg"] == "ahoj u stolu" for m in s["chat"])


def test_leave_refunds_bet_in_betting(client):
    h = _mk_user(1000); st = _run(bj_room.create, h, "host")
    rid = st["room_id"]
    p = _mk_user(1000); _run(bj_room.join, p, "p", st["code"])
    _run(bj_room.place_bet, p, rid, 200)
    assert _points(p) == 800
    _run(bj_room.leave, p, rid)
    assert _points(p) == 1000                               # nevyužitá sázka vrácena
