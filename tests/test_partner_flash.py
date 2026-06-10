"""Flash bonus pro partnerské odkazy: náhodná obnova (kolo) + claim 1× za KOLO.

DB je session-scoped → každý test si na začátku vyčistí kola (globální stav).

    .venv/Scripts/python.exe -m pytest tests/test_partner_flash.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso
from app import partners_flash


def _login_as(role="user"):
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
        return token, cur.lastrowid
    finally:
        conn.close()


def _hdr(token):
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _mk_link(mode="flash", reward=100, enabled=1):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO partner_links (label, url, reward, icon, enabled, mode, sort_order, created_at) "
            "VALUES (?,?,?,?,?,?,0,?)",
            (f"L{secrets.token_hex(3)}", "https://example.com", reward, "⚡", enabled, mode, now_iso()))
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


def _reset_rounds():
    conn = get_conn()
    try:
        conn.execute("DELETE FROM partner_rounds")
        conn.execute("DELETE FROM partner_flash_claims")
        conn.commit()
    finally:
        conn.close()


def _open_round():
    conn = get_conn()
    try:
        return partners_flash.open_round(conn, force=True)
    finally:
        conn.close()


def test_flash_not_claimable_without_round(client):
    _reset_rounds()
    token, uid = _login_as()
    lid = _mk_link(mode="flash", reward=300)
    r = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r.status_code == 400, "flash bez aktivního kola nesmí jít vyzvednout"
    assert _points(uid) == 0


def test_flash_claim_in_round_then_blocked_same_round(client):
    _reset_rounds()
    token, uid = _login_as()
    lid = _mk_link(mode="flash", reward=300)
    res = _open_round()
    assert res["ok"], res
    r = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r.status_code == 200, r.text
    assert _points(uid) == 300
    r2 = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r2.status_code == 400, "ve stejném kole podruhé ne"
    assert _points(uid) == 300


def test_flash_new_round_allows_again(client):
    _reset_rounds()
    token, uid = _login_as()
    lid = _mk_link(mode="flash", reward=100)
    _open_round()
    client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert _points(uid) == 100
    # nech aktuální kolo propadnout a otevři nové
    conn = get_conn()
    try:
        conn.execute("UPDATE partner_rounds SET expires_at = ?", (now_iso(),))
        conn.commit()
    finally:
        conn.close()
    assert _open_round()["ok"]
    r = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r.status_code == 200, r.text
    assert _points(uid) == 200, "nové kolo → další odměna"


def test_once_link_unaffected_by_rounds(client):
    _reset_rounds()
    token, uid = _login_as()
    lid = _mk_link(mode="once", reward=50)
    r = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))   # bez kola jde
    assert r.status_code == 200
    assert _points(uid) == 50
    r2 = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r2.status_code == 400, "once jen 1× navždy"
    assert _points(uid) == 50


def test_status_reports_flash_window(client):
    _reset_rounds()
    token, uid = _login_as()
    _mk_link(mode="flash", reward=100)
    d0 = client.get("/api/partner-links", headers=_hdr(token)).json()
    assert d0["flash_active"] is False
    _open_round()
    d1 = client.get("/api/partner-links", headers=_hdr(token)).json()
    assert d1["flash_active"] is True
    fl = [l for l in d1["links"] if l["mode"] == "flash"][0]
    assert fl["claimable"] is True


def test_admin_trigger_opens_round(client):
    _reset_rounds()
    token, _ = _login_as("admin")
    _mk_link(mode="flash", reward=100)
    r = client.post("/api/admin/economy/partner-flash/trigger", headers=_hdr(token))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # a teď to status hlásí jako běžící
    st = client.get("/api/admin/economy/partner-flash", headers=_hdr(token)).json()
    assert st["active"] is True
