"""Anticheat IP-whitelist: IP v config.TRUSTED_IPS musí mít vždy skóre 0 a nikdy
se neblokovat – i kdyby jinak spadla přes práh (např. sdílená/NAT IP). Důvod:
známé „dobré" sdílené IP (NAT operátora, síť streamera) jinak falešně spouští
pravidlo „sdílená IP" a zaplavují Discord alerty.

    .venv/Scripts/python.exe -m pytest tests/test_anticheat_trusted_ip.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app import anticheat
from app.config import TRUSTED_IPS
from app.db import get_conn, now_iso

TRUSTED_IP = "194.228.7.45"      # whitelistnutá (spamovala anticheat)
CONTROL_IP = "203.0.113.77"      # TEST-NET-3, NENÍ trusted (kontrola)


class _FakeReq:
    """Minimální náhrada za FastAPI Request – stačí .headers.get('fly-client-ip')."""
    def __init__(self, ip):
        self.headers = {"fly-client-ip": ip}
        self.client = None


def _make_user(conn, age_hours: float = 500):
    uname = f"u_{secrets.token_hex(4)}"
    created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    cur = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) "
        "VALUES (?, ?, 'user', 1000, ?)", (uname, uname, created))
    return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()


def _flood_shared_ip(conn, ip: str, n: int = 4):
    """Zaregistruje IP u n různých účtů → pravidlo 'sdílená IP' (>=3) má zabrat."""
    for _ in range(n):
        u = _make_user(conn)
        conn.execute(
            "INSERT INTO login_events (user_id, ip, user_agent, method, created_at) "
            "VALUES (?, ?, 'ua', 'test', ?)", (u["id"], ip, now_iso()))
    conn.commit()


def test_trusted_ip_is_configured():
    """Pojistka: konkrétní IP, kvůli které se whitelist přidával, tam fakt je."""
    assert TRUSTED_IP in TRUSTED_IPS


def test_trusted_ip_scores_zero_even_when_shared(client):
    """Trusted IP sdílená 4 účty → pořád skóre 0 a žádný blok."""
    conn = get_conn()
    try:
        _flood_shared_ip(conn, TRUSTED_IP, n=4)
        user = _make_user(conn)
        conn.commit()
        risk = anticheat.evaluate_risk(conn, user, _FakeReq(TRUSTED_IP), context="claim")
        assert risk["score"] == 0, f"trusted IP měla mít 0, má {risk['score']}: {risk['reasons']}"
        assert risk["block"] is False
        assert risk["soft"] is False
    finally:
        conn.close()


def test_control_ip_still_flagged_when_shared(client):
    """Kontrola: stejná situace na NE-trusted IP musí pořád skórovat > 0 –
    jinak by test výše nic nedokazoval (whitelist by mohl být no-op)."""
    conn = get_conn()
    try:
        _flood_shared_ip(conn, CONTROL_IP, n=4)
        user = _make_user(conn)
        conn.commit()
        risk = anticheat.evaluate_risk(conn, user, _FakeReq(CONTROL_IP), context="claim")
        assert risk["score"] > 0, "ne-trusted sdílená IP měla skórovat > 0"
    finally:
        conn.close()
