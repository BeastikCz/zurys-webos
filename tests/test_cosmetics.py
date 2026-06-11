"""Kosmetika: koupě (sink), vlastnictví, sub-gate, nasazení (toggle), resolve do payloadu.

    .venv/Scripts/python.exe -m pytest tests/test_cosmetics.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso
from app import cosmetics


def _mk(points: int = 0, role: str = "user", is_sub: int = 0):
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, is_sub, created_at) VALUES (?,?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", role, points, is_sub, now_iso()))
        tok = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (tok, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return cur.lastrowid, tok
    finally:
        conn.close()


def _hdr(tok):
    return {"Cookie": f"{SESSION_COOKIE}={tok}"}


def _row(uid):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()


def test_buy_debits_and_owns(client):
    uid, tok = _mk(points=5000)
    r = client.post("/api/cosmetics/buy", json={"key": "name_blue"}, headers=_hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 4000          # 5000 - 1000 (sink!)
    conn = get_conn()
    try:
        assert conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id=? AND item_key='name_blue'", (uid,)).fetchone()
    finally:
        conn.close()
    # podruhé už ne
    assert client.post("/api/cosmetics/buy", json={"key": "name_blue"}, headers=_hdr(tok)).status_code == 400


def test_not_enough_points(client):
    uid, tok = _mk(points=100)
    r = client.post("/api/cosmetics/buy", json={"key": "name_blue"}, headers=_hdr(tok))   # stojí 1000
    assert r.status_code == 400 and "dost" in r.json()["detail"].lower()
    assert _row(uid)["points"] == 100           # nic se nestrhlo


def test_sub_only_gate(client):
    _, tok = _mk(points=30000, is_sub=0)
    r = client.post("/api/cosmetics/buy", json={"key": "name_emerald"}, headers=_hdr(tok))
    assert r.status_code == 400 and "sub" in r.json()["detail"].lower()
    _, tok2 = _mk(points=30000, is_sub=1)
    assert client.post("/api/cosmetics/buy", json={"key": "name_emerald"}, headers=_hdr(tok2)).status_code == 200


def test_equip_toggle_and_resolve(client):
    uid, tok = _mk(points=5000)
    client.post("/api/cosmetics/buy", json={"key": "name_blue"}, headers=_hdr(tok))
    r = client.post("/api/cosmetics/equip", json={"key": "name_blue"}, headers=_hdr(tok))
    assert r.status_code == 200 and r.json()["equipped_key"] == "name_blue"
    assert cosmetics.resolve(_row(uid))["name"] == "cn-blue"
    # druhý klik = sundat (toggle)
    r2 = client.post("/api/cosmetics/equip", json={"key": "name_blue"}, headers=_hdr(tok))
    assert r2.json()["equipped_key"] is None
    assert cosmetics.resolve(_row(uid))["name"] == ""


def test_equip_requires_ownership(client):
    _, tok = _mk(points=0)
    r = client.post("/api/cosmetics/equip", json={"key": "name_blue"}, headers=_hdr(tok))
    assert r.status_code == 400 and "vlastn" in r.json()["detail"].lower()


def test_payloads_include_cos(client):
    uid, tok = _mk(points=5000)
    client.post("/api/cosmetics/buy", json={"key": "frame_bronze"}, headers=_hdr(tok))
    client.post("/api/cosmetics/equip", json={"key": "frame_bronze"}, headers=_hdr(tok))
    # list endpoint
    lst = client.get("/api/cosmetics", headers=_hdr(tok)).json()
    it = next(i for i in lst["items"] if i["key"] == "frame_bronze")
    assert it["owned"] is True and it["equipped"] is True
    # /auth/me přes to_public
    me = client.get("/api/auth/me", headers=_hdr(tok)).json()
    assert me["user"]["cos"]["frame"] == "cf-bronze"
    # leaderboard řádky mají cos
    lb = client.get("/api/leaderboard?limit=5").json()
    assert all("cos" in row for row in lb)


def test_refund_removed_banners(client):
    """Zrušené bannery: refund_removed vrátí cenu, smaže vlastnictví a sundá nasazené."""
    from app import cosmetics
    uid, _ = _mk(points=1000)
    conn = get_conn()
    try:
        conn.execute("INSERT INTO cosmetic_owns (user_id, item_key, acquired_at) VALUES (?,?,?)",
                     (uid, "banner_gold", now_iso()))
        conn.execute("UPDATE users SET cos_banner='banner_gold' WHERE id=?", (uid,))
        conn.commit()
        n = cosmetics.refund_removed(conn)
        conn.commit()
        assert n >= 1
        row = conn.execute("SELECT points, cos_banner FROM users WHERE id=?", (uid,)).fetchone()
        assert row["points"] == 1000 + 25000          # cena banner_gold vrácena
        assert row["cos_banner"] is None
        assert conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id=? AND item_key='banner_gold'", (uid,)).fetchone() is None
    finally:
        conn.close()
