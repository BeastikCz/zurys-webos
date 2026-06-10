"""Anti-funnel test pro Exchange dary.

Díra, kterou to hlídá: založ alt na čisté IP/zařízení → pošli body na hlavní účet
DŘÍV, než se stihne propojit otisk → _shared_identity to tehdy ještě nechytí.
Fix = nový účet (< GIFT_MIN_AGE_HOURS) nesmí vůbec darovat.

    .venv/Scripts/python.exe -m pytest tests/test_gift_antifarm.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

import pytest

from app.config import SESSION_COOKIE


@pytest.fixture(autouse=True)
def _enable_gifts(monkeypatch):
    """Dary jsou ve výchozím stavu VYPNUTÉ (GIFT_ENABLED=False). Pro testy anti-farma logiky
    je dočasně zapneme, ať testujeme samotnou ochranu, ne přepínač zap/vyp."""
    monkeypatch.setattr("app.routers.misc.GIFT_ENABLED", True)


def _make_user(role: str = "user", age_hours: float = 0, points: int = 10000):
    """Založí uživatele se zadaným stářím účtu + relací. Vrátí (token, username)."""
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        uname = f"{role}_{secrets.token_hex(4)}"
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (uname, uname, role, points, created))
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, cur.lastrowid, now_iso(),
             (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token, uname
    finally:
        conn.close()


def _gift(client, token: str, to_username: str, amount: int = 100):
    return client.post("/api/exchange/gift", json={"username": to_username, "amount": amount},
                       headers={"Cookie": f"{SESSION_COOKIE}={token}"})


def test_new_account_cannot_gift(client):
    """Čerstvý účet (0 h) NESMÍ poslat dar → 403 (anti-funnel přes alty)."""
    _, recipient = _make_user("user", age_hours=500)        # starý příjemce
    sender_token, _ = _make_user("user", age_hours=0)       # odesílatel = nový alt
    r = _gift(client, sender_token, recipient)
    assert r.status_code == 403, f"nový účet měl dostat 403, dostal {r.status_code}: {r.text}"


def test_old_account_can_gift(client):
    """Zavedený účet (>48 h) s body a bez sdílené identity dar pošle → 200."""
    _, recipient = _make_user("user", age_hours=500)
    sender_token, _ = _make_user("user", age_hours=500, points=10000)
    r = _gift(client, sender_token, recipient, amount=100)
    assert r.status_code == 200, f"zavedený účet měl projít, dostal {r.status_code}: {r.text}"


def _share_ip(username_a: str, username_b: str, ip: str = "203.0.113.50"):
    """Přiřadí oběma účtům stejnou IP (login_events) – simuluje stejnou síť."""
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        for uname in (username_a, username_b):
            uid = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()["id"]
            conn.execute("INSERT INTO login_events (user_id, ip, user_agent, method, created_at) "
                         "VALUES (?, ?, 'test-ua', 'test', ?)", (uid, ip, now_iso()))
        conn.commit()
    finally:
        conn.close()


def _share_device(username_a: str, username_b: str, fp: str = "testfp_shared_dev"):
    """Přiřadí oběma účtům stejný otisk zařízení (client_signals)."""
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        for uname in (username_a, username_b):
            uid = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()["id"]
            conn.execute("INSERT INTO client_signals (user_id, webdriver, fp_hash, ua, created_at) "
                         "VALUES (?, 0, ?, 'test-ua', ?)", (uid, fp, now_iso()))
        conn.commit()
    finally:
        conn.close()


def test_same_ip_cannot_gift(client):
    """Dva zavedené účty se SDÍLENOU IP → dar zablokován (403)."""
    sender_token, sender = _make_user("user", age_hours=500)
    _, recipient = _make_user("user", age_hours=500)
    _share_ip(sender, recipient)
    r = _gift(client, sender_token, recipient)
    assert r.status_code == 403, f"sdílená IP měla blokovat, dostal {r.status_code}: {r.text}"


def test_same_device_cannot_gift(client):
    """Dva zavedené účty se STEJNÝM zařízením (otisk) → dar zablokován (403)."""
    sender_token, sender = _make_user("user", age_hours=500)
    _, recipient = _make_user("user", age_hours=500)
    _share_device(sender, recipient)
    r = _gift(client, sender_token, recipient)
    assert r.status_code == 403, f"stejné zařízení mělo blokovat, dostal {r.status_code}: {r.text}"
