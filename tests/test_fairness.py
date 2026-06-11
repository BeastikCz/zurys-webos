"""Provably fair: matika (determinismus + rozložení) + end-to-end ověřitelnost kola.

    .venv/Scripts/python.exe -m pytest tests/test_fairness.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso
from app import fairness


def _mk(points: int = 0):
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", "user", points, now_iso()))
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return cur.lastrowid, tok
    finally:
        conn.close()


def _hdr(tok):
    return {"Cookie": f"{SESSION_COOKIE}={tok}"}


def test_math_deterministic_and_verifiable():
    ss = fairness.new_server_seed()
    assert fairness.weighted_index(ss, "c", 7, [1, 2, 3]) == fairness.weighted_index(ss, "c", 7, [1, 2, 3])
    assert fairness.seed_hash(ss) == fairness.seed_hash(ss)
    # různý nonce → (skoro vždy) jiný roll
    rolls = {fairness.roll_float(ss, "c", n) for n in range(5)}
    assert len(rolls) >= 4


def test_wheel_is_provably_fair(client):
    """3× zatočím, rotuju seed (odhalí server seed), pak KAŽDÝ spin přepočítám z odhaleného
    seedu → musí sedět index + commit. To je celá garance provably-fair."""
    uid, tok = _mk(points=0)
    weights = client.get("/api/fair/me", headers=_hdr(tok)).json()["wheel_weights"]

    spins = []
    for _ in range(3):
        conn = get_conn()
        conn.execute("UPDATE users SET last_wheel = NULL WHERE id = ?", (uid,))  # obejdi 20h cooldown
        conn.commit(); conn.close()
        r = client.post("/api/wheel/spin", headers=_hdr(tok)).json()
        assert "fair" in r and "server_hash" in r["fair"]
        spins.append((r["fair"]["nonce"], r["fair"]["client_seed"], r["fair"]["server_hash"], r["index"]))

    assert [s[0] for s in spins] == [0, 1, 2]                  # nonce roste

    rot = client.post("/api/fair/rotate", json={}, headers=_hdr(tok)).json()
    revealed = rot["revealed_server_seed"]

    for nonce, cs, server_hash, idx in spins:
        assert fairness.seed_hash(revealed) == server_hash     # commit nebyl měněn dodatečně
        assert fairness.weighted_index(revealed, cs, nonce, weights) == idx   # výsledek se reprodukuje


def test_rotate_sets_new_commit_and_resets_nonce(client):
    uid, tok = _mk()
    me1 = client.get("/api/fair/me", headers=_hdr(tok)).json()
    rot = client.post("/api/fair/rotate", json={"client_seed": "moje-volba-123"}, headers=_hdr(tok)).json()
    assert rot["revealed_server_hash"] == me1["server_hash"]   # odhalený = původní commit
    me2 = client.get("/api/fair/me", headers=_hdr(tok)).json()
    assert me2["server_hash"] == rot["new_server_hash"] != me1["server_hash"]
    assert me2["client_seed"] == "moje-volba-123" and me2["nonce"] == 0
