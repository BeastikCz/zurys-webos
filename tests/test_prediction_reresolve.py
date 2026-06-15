"""Oprava špatně vyhodnocené predikce (re-resolve): storno staré výplaty + výplata na správného vítěze.

    .venv/Scripts/python.exe -m pytest tests/test_prediction_reresolve.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _login(role: str) -> str:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"{role}_{suf}", f"{role}_{suf}", role, now_iso()))
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def _mkuser(points: int) -> int:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", "user", points, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _bal(uid: int) -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def test_reresolve_moves_payout_to_correct_winner(client):
    # Připrav predikci VYHODNOCENOU špatně na A: b1 vsadil 100 na A (výplata 200), b2 100 na B (0).
    b1, b2 = _mkuser(1000), _mkuser(1000)
    conn = get_conn()
    try:
        pid = conn.execute(
            "INSERT INTO predictions (question, game, status, created_at) VALUES ('Q','x','resolved',?)",
            (now_iso(),)).lastrowid
        optA = conn.execute("INSERT INTO prediction_options (prediction_id, label, position) VALUES (?,?,0)",
                            (pid, "A")).lastrowid
        optB = conn.execute("INSERT INTO prediction_options (prediction_id, label, position) VALUES (?,?,1)",
                            (pid, "B")).lastrowid
        conn.execute("UPDATE predictions SET winner_option_id=? WHERE id=?", (optA, pid))
        conn.execute("INSERT INTO prediction_bets (prediction_id, option_id, user_id, amount, payout, created_at) "
                     "VALUES (?,?,?,100,200,?)", (pid, optA, b1, now_iso()))
        conn.execute("INSERT INTO prediction_bets (prediction_id, option_id, user_id, amount, payout, created_at) "
                     "VALUES (?,?,?,100,0,?)", (pid, optB, b2, now_iso()))
        conn.commit()
    finally:
        conn.close()
    bal1, bal2 = _bal(b1), _bal(b2)

    r = client.post(f"/api/predictions/{pid}/reresolve", json={"option_id": optB}, headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text

    # b1 (špatně vyhrál) → storno −200; b2 (správný vítěz) → +200
    assert _bal(b1) == bal1 - 200, "b1 musí přijít o špatnou výhru"
    assert _bal(b2) == bal2 + 200, "b2 musí dostat správnou výhru"

    conn = get_conn()
    try:
        assert conn.execute("SELECT winner_option_id FROM predictions WHERE id=?", (pid,)).fetchone()[0] == optB
        pa = conn.execute("SELECT payout FROM prediction_bets WHERE prediction_id=? AND option_id=?", (pid, optA)).fetchone()[0]
        pb = conn.execute("SELECT payout FROM prediction_bets WHERE prediction_id=? AND option_id=?", (pid, optB)).fetchone()[0]
        assert pa == 0 and pb == 200, "výplaty v sázkách musí sedět na nového vítěze"
    finally:
        conn.close()


def test_prediction_exposes_creator(client):
    # staff (broadcaster) vytvoří predikci → create i veřejný výpis ji vrátí s autorem
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        uname = f"caster_{suf}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (uname, uname, "broadcaster", now_iso())).lastrowid
        token = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
    finally:
        conn.close()

    r = client.post("/api/predictions",
                    json={"question": "Vyhrajeme zápas?", "options": ["Ano", "Ne"], "game": "CS2", "lock_seconds": 0},
                    headers=_hdr(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["creator"] and body["creator"]["username"] == uname
    assert body["creator"]["role"] == "broadcaster"

    lst = client.get("/api/predictions").json()
    mine = [p for p in lst["active"] if p["id"] == body["id"]]
    assert mine and mine[0]["creator"]["username"] == uname, "autor musí být i ve veřejném výpisu"


def test_reresolve_rejects_unresolved(client):
    conn = get_conn()
    try:
        pid = conn.execute("INSERT INTO predictions (question, game, status, created_at) VALUES ('Q','x','open',?)",
                           (now_iso(),)).lastrowid
        opt = conn.execute("INSERT INTO prediction_options (prediction_id, label, position) VALUES (?,?,0)",
                          (pid, "A")).lastrowid
        conn.commit()
    finally:
        conn.close()
    r = client.post(f"/api/predictions/{pid}/reresolve", json={"option_id": opt}, headers=_hdr(_login("admin")))
    assert r.status_code == 400, "re-resolve na nevyhodnocenou predikci musí selhat"
