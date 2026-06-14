"""Komunitní SUB cíl: společná lišta se plní z Kick subů (sub/resub = +1, gift sub = +n).
Když se naplní, VŠICHNI dnešní aktivní diváci (kdo sledoval nebo kecal) dostanou odměnu
+ bot to oznámí v chatu. Reset každý den. Stav/konfig v app_settings.

Flywheel: víc subů → cíl se naplní → všichni diváci berou → motivace subnout/giftnout.
Sourozenec community_goal.py (chat cíl) – stejný vzor.
"""
from .db import now_iso, get_setting, set_setting, local_date

DEFAULT_TARGET = 20      # kolik subů za den naplní cíl
DEFAULT_REWARD = 300     # kolik sedláků dostane každý dnešní aktivní divák


def _today() -> str:
    return local_date()          # den podle českého času


def _int(conn, key: str, default: int) -> int:
    v = get_setting(conn, key)
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _cfg(conn) -> dict:
    return {
        "enabled": _int(conn, "subgoal_enabled", 1),
        "target": max(1, _int(conn, "subgoal_target", DEFAULT_TARGET)),
        "reward": max(0, _int(conn, "subgoal_reward", DEFAULT_REWARD)),
    }


def _ensure_day(conn) -> None:
    """Nový den → vynuluj počítadlo i příznak výplaty."""
    if get_setting(conn, "subgoal_day") != _today():
        set_setting(conn, "subgoal_day", _today())
        set_setting(conn, "subgoal_progress", "0")
        set_setting(conn, "subgoal_done", "0")


def status(conn) -> dict:
    """Stav cíle pro UI lištu (veřejné)."""
    _ensure_day(conn)
    cfg = _cfg(conn)
    progress = _int(conn, "subgoal_progress", 0)
    done = get_setting(conn, "subgoal_done") == "1"
    conn.commit()
    return {
        "enabled": bool(cfg["enabled"]),
        "progress": min(progress, cfg["target"]),
        "target": cfg["target"],
        "reward": cfg["reward"],
        "done": done,
        "pct": min(100, round(progress * 100 / cfg["target"])) if cfg["target"] else 0,
    }


def tick(conn, count: int = 1) -> None:
    """+count subů do cíle. Po překročení atomicky 'claimne' výplatu a rozdá ji.
    Necommituje increment (commituje caller); _fire si commit dělá sám."""
    if count <= 0:
        return
    cfg = _cfg(conn)
    if not cfg["enabled"]:
        return
    _ensure_day(conn)
    conn.execute(
        "UPDATE app_settings SET value = CAST(COALESCE(value,'0') AS INTEGER) + ?, updated_at = ? "
        "WHERE key = 'subgoal_progress'", (count, now_iso()))
    if _int(conn, "subgoal_progress", 0) >= cfg["target"]:
        _fire(conn, cfg)


# dnes aktivní = měl dnes pohyb (sledoval nebo kecal)
_ACTIVE_WHERE = "day = ? AND (watch_today > 0 OR chat_today > 0)"


def _fire(conn, cfg) -> None:
    """Atomicky claimni výplatu (jen jednou za den) a rozdej všem dnešním aktivním divákům."""
    cur = conn.execute(
        "UPDATE app_settings SET value = '1', updated_at = ? WHERE key = 'subgoal_done' AND value != '1'",
        (now_iso(),))
    if cur.rowcount == 0:
        return                                   # už vyplaceno dnes (race)
    today, reward = _today(), cfg["reward"]
    conn.execute(
        f"UPDATE users SET points = points + ? WHERE id IN "
        f"(SELECT user_id FROM activity_state WHERE {_ACTIVE_WHERE})", (reward, today))
    conn.execute(
        f"INSERT INTO points_log (user_id, change, reason, created_at) "
        f"SELECT user_id, ?, 'Sub cíl komunity 🟣', ? FROM activity_state WHERE {_ACTIVE_WHERE}",
        (reward, now_iso(), today))
    n = conn.execute(
        f"SELECT COUNT(*) c FROM activity_state WHERE {_ACTIVE_WHERE}", (today,)).fetchone()["c"]
    conn.commit()
    try:
        from . import kickbot
        kickbot.send_message(
            conn, f"🟣 KOMUNITA SPLNILA SUB CÍL! {n} aktivních diváků právě bere +{reward} sedláků! "
                  f"Díky za subscribe! 🌾", kind="system")
    except Exception:
        import traceback
        traceback.print_exc()
