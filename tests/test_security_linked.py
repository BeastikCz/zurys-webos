"""Admin nástroj „propojené účty": cluster přes stejné zařízení (fingerprint) nebo IP.

    .venv/Scripts/python.exe -m pytest tests/test_security_linked.py -v
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


def _mkuser(points=0) -> int:
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


def _sig(uid, fp):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO client_signals (user_id, webdriver, fp_hash, ua, created_at) VALUES (?,0,?,?,?)",
                     (uid, fp, "ua", now_iso()))
        conn.commit()
    finally:
        conn.close()


def _login_event(uid, ip):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO login_events (user_id, ip, user_agent, method, created_at) VALUES (?,?,?,?,?)",
                     (uid, ip, "ua", "kick", now_iso()))
        conn.commit()
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def test_linked_by_device_and_ip(client):
    a, b, c, d = _mkuser(100), _mkuser(200), _mkuser(50), _mkuser(0)
    fp = "dev_" + secrets.token_hex(8)
    ip = "ip_" + secrets.token_hex(8)
    _sig(a, fp); _sig(b, fp)                      # a + b = stejné zařízení
    _login_event(a, ip); _login_event(c, ip)      # a + c = stejná IP
    # d je nepropojený
    r = client.get(f"/api/admin/security/linked/{a}", headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text
    data = r.json()
    ids = {x["id"]: x for x in data["accounts"]}
    assert a in ids and b in ids and c in ids, "a,b (zařízení) i c (IP) musí být v clusteru"
    assert d not in ids, "nepropojený účet tam nesmí být"
    assert ids[b]["same_device"] and not ids[b]["same_ip"]
    assert ids[c]["same_ip"] and not ids[c]["same_device"]
    assert ids[a]["is_self"]


def test_linked_is_admin_only(client):
    a = _mkuser()
    r = client.get(f"/api/admin/security/linked/{a}", headers=_hdr(_login("user")))
    assert r.status_code == 403, f"security sekce je jen pro admina, dostal {r.status_code}"


def test_linked_unknown_user_404(client):
    r = client.get("/api/admin/security/linked/99999999", headers=_hdr(_login("admin")))
    assert r.status_code == 404


def test_negatives_lists_negative_balance(client):
    """Endpoint mínusů vrátí účet se záporným zůstatkem (po opravě predikce / clawbacku)."""
    uid = _mkuser(-50)
    r = client.get("/api/admin/security/negatives", headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text
    assert uid in [u["id"] for u in r.json()["users"]], "záporný účet musí být v seznamu mínusů"
