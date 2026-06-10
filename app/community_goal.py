"""Komunitní chat cíl: společná lišta se plní z aktivity chatu (anti-spam: +1 za
GENUINE zprávu po cooldownu, ne za spam – tick volá award_chat až po projití
cooldownu). Když se naplní, VŠICHNI dnešní chat-přispěvatelé dostanou odměnu +
bot to oznámí v chatu. Reset každý den (UTC). Stav/konfig v app_settings.

Flywheel: víc lidí kecá → cíl se naplní → všichni berou → Zurys vydělá na aktivitě.
"""
from datetime import datetime, timezone

from .db import now_iso, get_setting, set_setting, local_date

DEFAULT_TARGET = 600     # kolik genuine chat-příspěvků za den naplní cíl
DEFAULT_REWARD = 500     # kolik sedláků dostane každý dnešní aktivní divák


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
        "enabled": _int(conn, "cgoal_enabled", 1),
        "target": max(1, _int(conn, "cgoal_target", DEFAULT_TARGET)),
        "reward": max(0, _int(conn, "cgoal_reward", DEFAULT_REWARD)),
    }


def _ensure_day(conn) -> None:
    """Nový den → vynuluj počítadlo i příznak výplaty."""
    if get_setting(conn, "cgoal_day") != _today():
        set_setting(conn, "cgoal_day", _today())
        set_setting(conn, "cgoal_progress", "0")
        set_setting(conn, "cgoal_done", "0")


def status(conn) -> dict:
    """Stav cíle pro UI lištu (veřejné)."""
    _ensure_day(conn)
    cfg = _cfg(conn)
    progress = _int(conn, "cgoal_progress", 0)
    done = get_setting(conn, "cgoal_done") == "1"
    conn.commit()
    return {
        "enabled": bool(cfg["enabled"]),
        "progress": min(progress, cfg["target"]),
        "target": cfg["target"],
        "reward": cfg["reward"],
        "done": done,
        "pct": min(100, round(progress * 100 / cfg["target"])) if cfg["target"] else 0,
    }


def tick(conn) -> None:
    """+1 za genuine chat zprávu. Po překročení cíle atomicky 'claimne' výplatu a rozdá ji."""
    cfg = _cfg(conn)
    if not cfg["enabled"]:
        return
    _ensure_day(conn)
    conn.execute(
        "UPDATE app_settings SET value = CAST(COALESCE(value,'0') AS INTEGER) + 1, updated_at = ? "
        "WHERE key = 'cgoal_progress'", (now_iso(),))
    if _int(conn, "cgoal_progress", 0) >= cfg["target"]:
        _fire(conn, cfg)


def _fire(conn, cfg) -> None:
    """Atomicky claimni výplatu (jen jednou za den) a rozdej ji všem dnešním chat-přispěvatelům."""
    cur = conn.execute(
        "UPDATE app_settings SET value = '1', updated_at = ? WHERE key = 'cgoal_done' AND value != '1'",
        (now_iso(),))
    if cur.rowcount == 0:
        return                                   # už vyplaceno dnes (race: jiná zpráva to právě dělá)
    today, reward = _today(), cfg["reward"]
    conn.execute(
        "UPDATE users SET points = points + ? WHERE id IN "
        "(SELECT user_id FROM activity_state WHERE day = ? AND chat_today > 0)", (reward, today))
    conn.execute(
        "INSERT INTO points_log (user_id, change, reason, created_at) "
        "SELECT user_id, ?, 'Komunitní chat cíl 🎉', ? FROM activity_state WHERE day = ? AND chat_today > 0",
        (reward, now_iso(), today))
    n = conn.execute(
        "SELECT COUNT(*) c FROM activity_state WHERE day = ? AND chat_today > 0", (today,)).fetchone()["c"]
    conn.commit()
    try:
        from . import kickbot
        kickbot.send_message(
            conn, f"🎉 CHAT NAPLNIL DNEŠNÍ CÍL! {n} aktivních diváků právě bere +{reward} sedláků! "
                  f"Díky že kecáte! 💬🌾", kind="system")
    except Exception:
        import traceback
        traceback.print_exc()
