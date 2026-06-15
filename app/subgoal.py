"""Komunitní SUB cíl: společná lišta se plní z Kick subů (sub/resub = +1, gift sub = +n).
Když se naplní, odměnu dostanou JEN dnešní gifteři z happy hour (kdo dnes giftnul aspoň
1 sub během happy hour) + bot to oznámí v chatu. Reset každý den. Stav/konfig v app_settings,
seznam dnešních gifterů v tabulce subgoal_gifters.

Flywheel: happy hour → giftni suby → naplň cíl → gifteři berou odměnu → motivace giftnout
právě v happy hour. Sourozenec community_goal.py (chat cíl) – plní se stejně, jen odměnu
tam berou všichni aktivní (sub cíl ji cílí na giftery).
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
    """Nový den → vynuluj počítadlo, příznak výplaty i seznam dnešních gifterů."""
    if get_setting(conn, "subgoal_day") != _today():
        set_setting(conn, "subgoal_day", _today())
        set_setting(conn, "subgoal_progress", "0")
        set_setting(conn, "subgoal_done", "0")
        conn.execute("DELETE FROM subgoal_gifters WHERE day != ?", (_today(),))


def status(conn) -> dict:
    """Stav cíle pro UI lištu (veřejné)."""
    _ensure_day(conn)
    cfg = _cfg(conn)
    progress = _int(conn, "subgoal_progress", 0)
    done = get_setting(conn, "subgoal_done") == "1"
    gifters = conn.execute(
        "SELECT COUNT(*) c FROM subgoal_gifters WHERE day = ? AND hh_subs > 0", (_today(),)
    ).fetchone()["c"]
    conn.commit()
    return {
        "enabled": bool(cfg["enabled"]),
        "progress": min(progress, cfg["target"]),
        "target": cfg["target"],
        "reward": cfg["reward"],
        "done": done,
        "gifters": gifters,        # kolik dnešních HH gifterů odměnu vezme (zatím)
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


def record_gifter(conn, user_id: int, n: int, in_hh: bool) -> None:
    """Zaznamenej dnešního giftera subů (kolik subů celkem, z toho v happy hour).
    Volá kickevents při gift sub eventu PŘED tickem (ať je gifter v outpayu, i kdyby
    cíl naplnil právě jeho gift). Necommituje – commit dělá caller / _fire."""
    if not user_id or n <= 0:
        return
    _ensure_day(conn)
    hh = n if in_hh else 0
    conn.execute(
        "INSERT INTO subgoal_gifters (day, user_id, subs, hh_subs) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(day, user_id) DO UPDATE SET subs = subs + ?, hh_subs = hh_subs + ?",
        (_today(), user_id, n, hh, n, hh))


def _fire(conn, cfg) -> None:
    """Atomicky claimni výplatu (jen jednou za den) a rozdej JEN dnešním gifterům z happy hour."""
    cur = conn.execute(
        "UPDATE app_settings SET value = '1', updated_at = ? WHERE key = 'subgoal_done' AND value != '1'",
        (now_iso(),))
    if cur.rowcount == 0:
        return                                   # už vyplaceno dnes (race)
    today, reward = _today(), cfg["reward"]
    ids = [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM subgoal_gifters WHERE day = ? AND hh_subs > 0", (today,)).fetchall()]
    if ids and reward > 0:
        qm = ",".join("?" * len(ids))
        conn.execute(f"UPDATE users SET points = points + ? WHERE id IN ({qm})", [reward, *ids])
        conn.executemany(
            "INSERT INTO points_log (user_id, change, reason, created_at) "
            "VALUES (?, ?, 'Sub cíl komunity 🟣🎁', ?)",
            [(uid, reward, now_iso()) for uid in ids])
    n = len(ids)
    conn.commit()
    try:
        from . import kickbot
        if n > 0:
            who = "gifter" if n == 1 else "gifterů"
            kickbot.send_message(
                conn, f"🟣 KOMUNITA SPLNILA SUB CÍL! {n} {who} z happy hour bere +{reward} sedláků! "
                      f"Díky za gift suby! 🎁🌾", kind="system")
        else:
            kickbot.send_message(
                conn, "🟣 SUB CÍL SPLNĚN! Dnes ale nikdo nedaroval sub v happy hour, "
                      "takže odměna propadá – příště giftněte během happy hour! 🎁", kind="system")
    except Exception:
        import traceback
        traceback.print_exc()
