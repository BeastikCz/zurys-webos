"""CaseHug vklady: ruční odměna admin/broadcaster klikem (screen ověřen na Discordu).

Testuje: připsání (sedláci + XP supporter), dedup 10 min (409 + force), validaci presetu,
role gating (broadcaster ano, mod/user ne).

    .venv/Scripts/python.exe -m pytest tests/test_casehug.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _mk_user(role="user", points=0):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"ch_{suf}", f"ch_{suf}", role, points, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _login(role):
    from app.db import get_conn, now_iso
    uid = _mk_user(role)
    conn = get_conn()
    try:
        token = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def _post(client, token, body):
    body.setdefault("deposit_id", "dep_" + secrets.token_hex(4))   # unikátní ID vkladu (povinné)
    return client.post("/api/admin/casehug/award", json=body,
                       headers={"Cookie": f"{SESSION_COOKIE}={token}"})


def _user_row(uid):
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn.close()


def test_award_grants_points_and_supporter_xp(client):
    token = _login("broadcaster")
    uid = _mk_user()
    r = _post(client, token, {"user_id": uid, "eur": 10})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["points"] == 1000 and d["xp"] == 1000
    row = _user_row(uid)
    assert row["points"] == 1000
    assert row["earned_total"] == 1000          # XP explicitně (supporter), NE přes farm cap


def test_award_dedup_and_force(client):
    token = _login("broadcaster")
    uid = _mk_user()
    assert _post(client, token, {"user_id": uid, "eur": 5}).status_code == 200
    r2 = _post(client, token, {"user_id": uid, "eur": 5})
    assert r2.status_code == 409                # stejný preset do 10 min = dvojklik guard
    r3 = _post(client, token, {"user_id": uid, "eur": 5, "force": True})
    assert r3.status_code == 200                # force přebije
    assert _user_row(uid)["points"] == 2 * 500
    # jiný preset dedup neblokuje
    assert _post(client, token, {"user_id": uid, "eur": 2}).status_code == 200


def test_award_custom_amount_and_invalid(client):
    token = _login("broadcaster")
    uid = _mk_user()
    # vlastní částka mimo presety: kurz 100/€
    r = _post(client, token, {"user_id": uid, "eur": 7})
    assert r.status_code == 200 and r.json()["points"] == 700 and r.json()["xp"] == 700
    assert _post(client, token, {"user_id": uid, "eur": 0}).status_code == 400
    assert _post(client, token, {"user_id": uid, "eur": 501}).status_code == 400
    assert _post(client, token, {"user_id": 99999999, "eur": 10}).status_code == 404


def test_undo_reverses_and_frees_deposit_id(client):
    token = _login("broadcaster")
    uid = _mk_user()
    from app.db import get_conn
    r = _post(client, token, {"user_id": uid, "eur": 10, "deposit_id": "undo_test1"})
    assert r.status_code == 200
    conn = get_conn()
    try:
        log_id = conn.execute("SELECT id FROM points_log WHERE user_id=? AND reason LIKE 'Vklad CaseHug %'",
                              (uid,)).fetchone()["id"]
        before = conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn.close()
    ru = client.post("/api/admin/casehug/undo", json={"log_id": log_id},
                     headers={"Cookie": f"{SESSION_COOKIE}={token}"})
    assert ru.status_code == 200 and ru.json()["reversed"] == 1000
    conn = get_conn()
    try:
        after = conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
        assert after["points"] == before["points"] - 1000
        assert after["earned_total"] == before["earned_total"] - 1000
        assert not conn.execute("SELECT 1 FROM points_log WHERE id=?", (log_id,)).fetchone()
    finally:
        conn.close()
    # deposit ID je volné → jde připsat znovu (správná částka)
    assert _post(client, token, {"user_id": uid, "eur": 20, "deposit_id": "undo_test1"}).status_code == 200
    # neexistující / ne-casehug log_id → 404
    assert client.post("/api/admin/casehug/undo", json={"log_id": 99999999},
                       headers={"Cookie": f"{SESSION_COOKIE}={token}"}).status_code == 404


def test_award_deposit_id_required_and_unique(client):
    token = _login("broadcaster")
    uid, uid2 = _mk_user(), _mk_user()
    assert _post(client, token, {"user_id": uid, "eur": 2, "deposit_id": ""}).status_code == 400
    assert _post(client, token, {"user_id": uid, "eur": 2, "deposit_id": "ab"}).status_code == 400
    assert _post(client, token, {"user_id": uid, "eur": 2, "deposit_id": "0bc0e3ba"}).status_code == 200
    # stejné ID podruhé = recyklovaný screen, blokuje i pro JINÉHO uživatele a force nepřebije
    r = _post(client, token, {"user_id": uid2, "eur": 5, "deposit_id": "0bc0e3ba", "force": True})
    assert r.status_code == 409 and "0bc0e3ba" in r.json()["detail"]


def test_award_role_gating(client):
    uid = _mk_user()
    assert _post(client, _login("mod"), {"user_id": uid, "eur": 2}).status_code == 403
    assert _post(client, _login("user"), {"user_id": uid, "eur": 2}).status_code == 403
    assert _post(client, _login("admin"), {"user_id": uid, "eur": 2}).status_code == 200


def test_award_feeds_crew_as_supporter(client):
    """Vklad do crew NEpočítá (rozhodnutí 10.7.) – crew XP se nesmí hnout."""
    from app.db import get_conn
    from app import crews
    token = _login("broadcaster")
    uid = _mk_user(points=100000)
    conn = get_conn()
    try:
        st = crews.create(conn, uid, "user", "CaseHug Parta", "CHP")
        cid = st["id"]
        before = conn.execute("SELECT xp FROM crews WHERE id=?", (cid,)).fetchone()["xp"]
    finally:
        conn.close()
    assert _post(client, token, {"user_id": uid, "eur": 20}).status_code == 200
    conn = get_conn()
    try:
        after = conn.execute("SELECT xp FROM crews WHERE id=?", (cid,)).fetchone()["xp"]
    finally:
        conn.close()
    assert after - before == 0                  # vklad crew nekrmí
