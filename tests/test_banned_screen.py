import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def test_banned_account_gets_full_screen_and_admin_message(client):
    conn = get_conn()
    try:
        username = "banned_" + secrets.token_hex(4)
        user_id = conn.execute(
            "INSERT INTO users (kick_username,username,role,banned,ban_reason,created_at) VALUES (?,?,?,1,?,?)",
            (username, username, "user", "Permanently banned", now_iso()),
        ).lastrowid
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
            (token, user_id, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
    me = client.get("/api/auth/me", headers=headers)
    blocked = client.get("/api/me/claims", headers=headers)
    js = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")

    assert me.status_code == 200 and me.json()["user"]["banned"] is True
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Tvůj účet byl zablokován (anticheat). Kontaktuj ADMINA."
    assert 'if (accountBlocked()) { renderBannedPage(); return; }' in js
    assert "Permanently banned" in js and "kontaktuj <strong>ADMINA</strong>" in js
    assert "state.user.banned || state.user.timeout_until" in js
    assert 'const title = timeout ? "Timeout" : "Permanently banned"' in js
    assert "timeout_reason" in js and "<strong>Důvod:</strong>" in js
    assert 'id="banTimeoutLeft"' in js and "location.reload()" in js
    assert "navigator.serviceWorker.getRegistration()" in js


def test_admin_ban_preserves_target_session_for_ban_screen(client):
    conn = get_conn()
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        admin_name = "admin_" + secrets.token_hex(4)
        target_name = "target_" + secrets.token_hex(4)
        admin_id = conn.execute(
            "INSERT INTO users (kick_username,username,role,created_at) VALUES (?,?,?,?)",
            (admin_name, admin_name, "admin", now_iso()),
        ).lastrowid
        target_id = conn.execute(
            "INSERT INTO users (kick_username,username,role,created_at) VALUES (?,?,?,?)",
            (target_name, target_name, "user", now_iso()),
        ).lastrowid
        admin_token = secrets.token_hex(24)
        target_token = secrets.token_hex(24)
        conn.executemany(
            "INSERT INTO sessions (token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
            [
                (admin_token, admin_id, now_iso(), expires_at),
                (target_token, target_id, now_iso(), expires_at),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    banned = client.post(
        f"/api/admin/users/{target_id}/ban",
        json={"banned": True, "reason": "TEST"},
        headers={"Cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    target_headers = {"Cookie": f"{SESSION_COOKIE}={target_token}"}
    me = client.get("/api/auth/me", headers=target_headers)
    blocked = client.get("/api/me/claims", headers=target_headers)

    assert banned.status_code == 200, banned.text
    assert me.status_code == 200
    assert me.json()["user"]["banned"] is True
    assert me.json()["user"]["ban_reason"] == "TEST"
    assert blocked.status_code == 403
