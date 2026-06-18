"""Timeout (dočasný blok webu + zrcadlení do Kick chatu).

Ověřuje: během timeoutu padá require_user na 403; 'off' i vypršení odemkne; admin je imunní;
timeout smí dát jen broadcaster+admin (mod ne). Kick mirror je mock/skip (účty bez kick_id).

    .venv/Scripts/python.exe -m pytest tests/test_timeout.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _make_user(role: str = "user", points: int = 1000):
    """Založí uživatele + relaci. Vrátí (token, username, id)."""
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        uname = f"{role}_{secrets.token_hex(4)}"
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (uname, uname, role, points, now_iso()))
        uid = cur.lastrowid
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token, uname, uid
    finally:
        conn.close()


def _hdr(token):
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _set_timeout_until(uid, iso):
    """Přímý zápis timeout_until (simuluje nastavený/vypršelý timeout bez Kick mirroru)."""
    from app.db import get_conn
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET timeout_until = ? WHERE id = ?", (iso, uid))
        conn.commit()
    finally:
        conn.close()


# require_user endpoint pro ověření blokace (GET, jednoduchý)
PROTECTED = "/api/notifications/unread"


def test_normal_user_not_blocked(client):
    """Bez timeoutu projde require_user (200)."""
    tok, _, _ = _make_user()
    r = client.get(PROTECTED, headers=_hdr(tok))
    assert r.status_code == 200, r.text


def test_active_timeout_blocks_site(client):
    """Aktivní timeout (do budoucna) → require_user vrátí 403."""
    tok, _, uid = _make_user()
    _set_timeout_until(uid, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    r = client.get(PROTECTED, headers=_hdr(tok))
    assert r.status_code == 403, f"timeout měl blokovat web, dostal {r.status_code}: {r.text}"
    assert "timeout" in r.text.lower()


def test_expired_timeout_unblocks(client):
    """Vypršelý timeout (v minulosti) → uživatel zase projde (200), bez zásahu admina."""
    tok, _, uid = _make_user()
    _set_timeout_until(uid, (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    r = client.get(PROTECTED, headers=_hdr(tok))
    assert r.status_code == 200, f"vypršelý timeout neměl blokovat, dostal {r.status_code}: {r.text}"


def test_admin_immune_to_timeout(client):
    """I kdyby měl admin nastavený timeout, web mu nepadá (admin je imunní)."""
    tok, _, uid = _make_user("admin")
    _set_timeout_until(uid, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    r = client.get(PROTECTED, headers=_hdr(tok))
    assert r.status_code == 200, f"admin měl být imunní, dostal {r.status_code}: {r.text}"


def test_admin_can_set_and_clear_timeout(client):
    """Admin nastaví timeout (target dostane 403) a 'off' ho zase odemkne (200). kick_id chybí → skip mirror."""
    admin_tok, _, _ = _make_user("admin")
    target_tok, _, target_id = _make_user()
    # nastav 1h timeout
    r = client.post(f"/api/admin/users/{target_id}/timeout", json={"duration": "1h"}, headers=_hdr(admin_tok))
    assert r.status_code == 200, r.text
    assert r.json()["timeout_until"] is not None
    assert client.get(PROTECTED, headers=_hdr(target_tok)).status_code == 403, "po nastavení má být blok"
    # zruš
    r = client.post(f"/api/admin/users/{target_id}/timeout", json={"duration": "off"}, headers=_hdr(admin_tok))
    assert r.status_code == 200, r.text
    assert r.json()["timeout_until"] is None
    assert client.get(PROTECTED, headers=_hdr(target_tok)).status_code == 200, "po 'off' má být zase přístup"


def test_invalid_duration_rejected(client):
    """Neplatná délka → 400 (whitelist 5m/15m/1h/6h/24h/7d/off)."""
    admin_tok, _, _ = _make_user("admin")
    _, _, target_id = _make_user()
    r = client.post(f"/api/admin/users/{target_id}/timeout", json={"duration": "99x"}, headers=_hdr(admin_tok))
    assert r.status_code in (400, 422), f"neplatná délka měla spadnout, dostal {r.status_code}: {r.text}"


def test_mod_cannot_timeout(client):
    """Moderátor nesmí dávat timeout (jen broadcaster+admin, jako ban) → 403."""
    mod_tok, _, _ = _make_user("mod")
    _, _, target_id = _make_user()
    r = client.post(f"/api/admin/users/{target_id}/timeout", json={"duration": "1h"}, headers=_hdr(mod_tok))
    assert r.status_code == 403, f"mod neměl mít právo, dostal {r.status_code}: {r.text}"


def test_cannot_timeout_admin(client):
    """Admina nelze umlčet → 400."""
    admin_tok, _, _ = _make_user("admin")
    _, _, victim_admin_id = _make_user("admin")
    r = client.post(f"/api/admin/users/{victim_admin_id}/timeout", json={"duration": "1h"}, headers=_hdr(admin_tok))
    assert r.status_code == 400, f"admin nemá jít umlčet, dostal {r.status_code}: {r.text}"
