"""Monitoring (#2 digest, #3 alert na admin akci z neznámé IP).

    .venv/Scripts/python.exe -m pytest tests/test_monitoring.py -v
"""
import secrets

from app import digest, deps
from app.db import get_conn, now_iso


class _FakeReq:
    """Minimální Request – stačí .headers.get('fly-client-ip')."""
    def __init__(self, ip):
        self.headers = {"fly-client-ip": ip}
        self.client = None


def _make_admin(conn):
    uname = f"adm_{secrets.token_hex(4)}"
    cur = conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) "
        "VALUES (?, ?, 'admin', 0, ?)", (uname, uname, now_iso()))
    return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()


def test_digest_compose_runs(client):
    """compose() proběhne nad reálným schématem a vrátí neprázdný souhrn."""
    conn = get_conn()
    try:
        conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) "
                     "VALUES (?,?,?,?,?)", (f"dg_{secrets.token_hex(3)}", "dg_x", "user", 100, now_iso()))
        conn.commit()
        text = digest.compose(conn)
        assert isinstance(text, str) and len(text) > 20
        assert "účt" in text.lower()        # řádek o nových účtech
        assert "oběh" in text.lower()       # řádek o bodech v oběhu
    finally:
        conn.close()


def test_admin_action_from_unknown_ip_alerts(client, monkeypatch):
    """Citlivá admin akce z IP mimo historii přihlášení → alert; ze známé → ne."""
    sent = []
    monkeypatch.setattr(deps, "ALERT_ON_ADMIN_NEW_IP", True)   # feature je default OFF, pro test ji zapneme
    monkeypatch.setattr(deps.alerts, "send", lambda *a, **k: sent.append(a[0] if a else ""))
    conn = get_conn()
    try:
        admin = _make_admin(conn)
        conn.execute("INSERT INTO login_events (user_id, ip, user_agent, method, created_at) "
                     "VALUES (?,?,?,?,?)", (admin["id"], "1.2.3.4", "ua", "test", now_iso()))
        conn.commit()
        # ze ZNÁMÉ IP (je v login_events) → žádný „neznámá IP" alert
        deps.record_audit(conn, admin, _FakeReq("1.2.3.4"), "user.role", "x", "y")
        assert not any("NEZNAME" in str(s) for s in sent), "ze známé IP neměl být alert"
        # z NEZNÁMÉ IP → alert přijde
        deps.record_audit(conn, admin, _FakeReq("9.9.9.9"), "user.role", "x", "y")
        assert any("NEZNAME" in str(s) for s in sent), "z neznámé IP měl přijít alert"
    finally:
        conn.close()


def test_digest_endpoint_requires_auth(client):
    """Ruční trigger digestu je admin-only → nepřihlášený dostane 401."""
    assert client.post("/api/admin/digest/test").status_code == 401
