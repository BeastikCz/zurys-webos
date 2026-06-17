"""Sezónní leaderboard: kdo nasbíral nejvíc sedláků tento měsíc (read-only z points_log).

    .venv/Scripts/python.exe -m pytest tests/test_season_leaderboard.py -v
"""
import secrets


def test_season_leaderboard(client):
    from app.db import get_conn, now_iso
    from app.routers import misc
    conn = get_conn()
    try:
        u = f"se_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (u, u, "user", now_iso())).lastrowid
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 1234, "Sledování streamu", now_iso()))     # zisk tento měsíc
        conn.commit()
    finally:
        conn.close()
    misc._season_cache["data"] = None     # vynuluj cache → čerstvá data vč. nového usera
    d = client.get("/api/leaderboard/season").json()
    assert "season" in d and isinstance(d["rows"], list)
    assert any(r["username"] == u and r["gained"] >= 1234 for r in d["rows"]), "uživatel má být v sezónním žebříčku"
