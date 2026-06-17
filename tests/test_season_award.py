"""Sezónní šampioni: na přelomu měsíce grant TOP 3 minulé sezóny exkluzivní rámeček.

    .venv/Scripts/python.exe -m pytest tests/test_season_award.py -v
"""
import secrets


def test_prev_season_and_bounds():
    from app import season_award
    assert season_award._prev_season("2026-01-15") == "2025-12"
    assert season_award._prev_season("2026-06-03") == "2026-05"
    lo, hi = season_award._month_bounds("2026-05")
    assert lo == "2026-05-01T00:00:00" and hi == "2026-06-01T00:00:00"


def test_season_baseline_no_retroactive(client):
    """První běh bez baseline → jen zapamatuje aktuální sezónu, nic neuděluje."""
    from app.db import get_conn
    from app import season_award
    conn = get_conn()
    try:
        conn.execute("DELETE FROM app_settings WHERE key = 'season_champ_start'")
        conn.commit()
        res = season_award.run(conn)
        assert res["awarded"] == 0 and res.get("note") == "baseline"
    finally:
        conn.close()


def test_season_champions_grant(client):
    """Top 3 minulé sezóny dostanou frame_champion; idempotentní."""
    from app.db import get_conn, now_iso, local_date, set_setting
    from app import season_award
    prev = season_award._prev_season(local_date())
    lo, _ = season_award._month_bounds(prev)
    mid = lo[:10] + "T12:00:00"           # datum uvnitř minulé sezóny
    conn = get_conn()
    try:
        u = f"champ_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (u, u, "user", now_iso())).lastrowid
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 9_999_999, "Sledování streamu", mid))   # obří zisk → TOP 1
        set_setting(conn, "season_champ_start", prev)              # start ≤ prev → prev se uděluje
        conn.execute("DELETE FROM app_settings WHERE key = ?", (f"season_champ_done_{prev}",))
        conn.commit()
    finally:
        conn.close()
    conn = get_conn()
    try:
        res = season_award.run(conn)
        assert res.get("awarded", 0) >= 1 and res.get("season") == prev
        assert conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id=? AND item_key=?",
                            (uid, season_award.CHAMP_FRAME)).fetchone(), "šampion má mít frame_champion"
        # idempotence: druhý běh už neuděluje
        assert season_award.run(conn).get("awarded", 0) == 0
    finally:
        conn.close()
