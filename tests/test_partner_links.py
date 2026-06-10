"""Partnerské/sponzorské odkazy v Bonusech: klik → JEDNORÁZOVÁ odměna (1× za uživatele).

Politika:
  * Uživatel vyzvedne každý odkaz jen 1× (atomicky, přes UNIQUE) – nejde farmit.
  * Vypnutý odkaz se nezobrazí ani nejde vyzvednout.
  * Správu (CRUD) smí jen admin + broadcaster (sekce economy), ne mod.
  * URL musí být http(s) (proti javascript: apod.).

    .venv/Scripts/python.exe -m pytest tests/test_partner_links.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _login_as(role: str):
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


def _mk_link(label="Sponzor", url="https://example.com", reward=100, enabled=1):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO partner_links (label, url, reward, icon, enabled, sort_order, created_at) "
            "VALUES (?,?,?,?,?,0,?)", (label, url, reward, "🤝", enabled, now_iso()))
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


def test_claim_credits_once(client):
    token, uid = _login_as("user")
    lid = _mk_link(reward=250)
    before = _points(uid)
    r = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r.status_code == 200, r.text
    assert r.json()["reward"] == 250
    assert _points(uid) == before + 250
    # podruhé už ne (UNIQUE → odmítnuto, body se nepřičtou znovu)
    r2 = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r2.status_code == 400, "druhý claim musí selhat"
    assert _points(uid) == before + 250, "body se nesmí přičíst dvakrát"


def test_list_shows_claimed_flag(client):
    token, uid = _login_as("user")
    lid = _mk_link(label="MujSponzor", reward=50)
    lst = client.get("/api/partner-links", headers=_hdr(token)).json()["links"]
    mine = [x for x in lst if x["id"] == lid]
    assert mine and mine[0]["claimed"] is False
    client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    lst2 = client.get("/api/partner-links", headers=_hdr(token)).json()["links"]
    mine2 = [x for x in lst2 if x["id"] == lid]
    assert mine2 and mine2[0]["claimed"] is True


def test_disabled_link_hidden_and_unclaimable(client):
    token, uid = _login_as("user")
    lid = _mk_link(enabled=0, reward=999)
    lst = client.get("/api/partner-links", headers=_hdr(token)).json()["links"]
    assert all(x["id"] != lid for x in lst), "vypnutý odkaz se nesmí zobrazit"
    before = _points(uid)
    r = client.post(f"/api/partner-links/{lid}/claim", headers=_hdr(token))
    assert r.status_code == 400, "vypnutý odkaz nejde vyzvednout"
    assert _points(uid) == before


def test_admin_crud(client):
    token, _ = _login_as("admin")
    r = client.post("/api/admin/economy/partner-links",
                    json={"label": "Sponzor A", "url": "https://a.example", "reward": 123},
                    headers=_hdr(token))
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    lst = client.get("/api/admin/economy/partner-links", headers=_hdr(token)).json()["links"]
    assert any(x["id"] == lid and x["reward"] == 123 for x in lst)
    r = client.post(f"/api/admin/economy/partner-links/{lid}",
                    json={"label": "Sponzor A2", "url": "https://a.example", "reward": 200, "enabled": False},
                    headers=_hdr(token))
    assert r.status_code == 200, r.text
    r = client.delete(f"/api/admin/economy/partner-links/{lid}", headers=_hdr(token))
    assert r.status_code == 200, r.text
    lst2 = client.get("/api/admin/economy/partner-links", headers=_hdr(token)).json()["links"]
    assert all(x["id"] != lid for x in lst2)


def test_admin_rejects_non_http_url(client):
    token, _ = _login_as("admin")
    r = client.post("/api/admin/economy/partner-links",
                    json={"label": "Zlo", "url": "javascript:alert(1)", "reward": 10},
                    headers=_hdr(token))
    assert r.status_code == 400, "non-http(s) URL musí být odmítnuta (XSS/scheme)"


def test_mod_cannot_manage(client):
    token, _ = _login_as("mod")
    r = client.post("/api/admin/economy/partner-links",
                    json={"label": "x", "url": "https://x.example", "reward": 1},
                    headers=_hdr(token))
    assert r.status_code == 403, f"BEZPEČNOST: mod nesmí spravovat partnerské odkazy, dostal {r.status_code}"
