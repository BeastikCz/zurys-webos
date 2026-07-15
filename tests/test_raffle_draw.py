import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import SESSION_COOKIE


def test_raffle_draw_is_single_step(client):
    from app.db import get_conn, now_iso

    conn = get_conn()
    try:
        suffix = secrets.token_hex(4)
        admin_id = conn.execute(
            "INSERT INTO users (kick_username, username, role, created_at) VALUES (?, ?, 'admin', ?)",
            (f"raffle_admin_{suffix}", f"raffle_admin_{suffix}", now_iso()),
        ).lastrowid
        entrant_name = f"raffle_user_{suffix}"
        entrant_id = conn.execute(
            "INSERT INTO users (kick_username, username, role, created_at) VALUES (?, ?, 'user', ?)",
            (entrant_name, entrant_name, now_iso()),
        ).lastrowid
        product_id = conn.execute(
            "INSERT INTO products (name, type, created_at) VALUES (?, 'raffle', ?)",
            (f"Raffle {suffix}", now_iso()),
        ).lastrowid
        conn.execute(
            "INSERT INTO raffle_entries (product_id, user_id, created_at) VALUES (?, ?, ?)",
            (product_id, entrant_id, now_iso()),
        )
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, admin_id, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        f"/api/admin/raffle/{product_id}/draw",
        headers={"Cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["winner"]["username"] == entrant_name

    app_js = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert "Commit (zamknout seed)" not in app_js
    assert "Vylosovat výherce" in app_js
