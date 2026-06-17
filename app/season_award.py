"""Sezónní šampioni: na přelomu měsíce grantne TOP 3 minulé sezóny exkluzivní rámeček
avataru ('frame_champion' z cosmetics, grant-only – nedá se koupit).

Bezpečné:
  * flag-gated per sezóna (season_champ_done_YYYY-MM) → grant proběhne 1× za sezónu;
  * start-baseline (season_champ_start) → NEuděluje retroaktivně staré měsíce; první
    udělená sezóna = ta, ve které se feature nasadila (vyhlášená až po jejím konci);
  * aditivní (jen INSERT do cosmetic_owns, nic se nemaže), idempotentní (kontrola vlastnictví).

Běží při startu (main.py). TOP 3 = nejvíc nasbíraných sedláků (kladný points_log) za měsíc.
"""
from .db import now_iso, get_setting, set_setting, local_date

CHAMP_FRAME = "frame_champion"
TOP_N = 3


def _prev_season(today_ymd: str) -> str:
    """'YYYY-MM' předchozího měsíce vůči dnešnímu datu."""
    y, m = int(today_ymd[:4]), int(today_ymd[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _month_bounds(season: str):
    """ISO hranice [start, konec) pro 'YYYY-MM'."""
    y, m = int(season[:4]), int(season[5:7])
    nxt = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
    return f"{season}-01T00:00:00", f"{nxt}-01T00:00:00"


def run(conn) -> dict:
    """Vyhlásí šampiony minulé sezóny (1× per sezóna). Volat při startu."""
    today = local_date()
    cur_season = today[:7]
    start = get_setting(conn, "season_champ_start")
    if not start:                                  # první běh: jen zapamatuj start, neuděluj nic
        set_setting(conn, "season_champ_start", cur_season)
        conn.commit()
        return {"awarded": 0, "note": "baseline"}
    prev = _prev_season(today)
    if prev < start or prev >= cur_season:         # starší než launch / ještě neskončila → nic
        return {"awarded": 0}
    flag = f"season_champ_done_{prev}"
    if get_setting(conn, flag):
        return {"awarded": 0}
    lo, hi = _month_bounds(prev)
    rows = conn.execute(
        "SELECT l.user_id AS uid FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.change > 0 AND l.created_at >= ? AND l.created_at < ? AND u.banned = 0 "
        "GROUP BY l.user_id ORDER BY SUM(l.change) DESC, u.username ASC LIMIT ?",
        (lo, hi, TOP_N)).fetchall()
    n = 0
    for r in rows:
        if not conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id = ? AND item_key = ?",
                            (r["uid"], CHAMP_FRAME)).fetchone():
            conn.execute("INSERT INTO cosmetic_owns (user_id, item_key, acquired_at) VALUES (?,?,?)",
                         (r["uid"], CHAMP_FRAME, now_iso()))
            n += 1
    set_setting(conn, flag, "done")
    conn.commit()
    return {"awarded": n, "season": prev}
