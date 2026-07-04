"""Komunitní chat cíl: společná lišta se plní z aktivity chatu (anti-spam: +1 za
GENUINE zprávu po cooldownu, ne za spam – tick volá award_chat až po projití
cooldownu). Když se naplní, VŠICHNI dnešní chat-přispěvatelé dostanou odměnu +
bot to oznámí v chatu. Reset na KONCI streamu (live_events), ne o půlnoci. Stav/konfig v app_settings.

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


def _session(conn) -> str:
    """Klíč RELACE chat cíle – mění se JEN na reset() (konec streamu), NE o půlnoci. Cíl se tak nuluje
    výhradně koncem streamu, ne přechodem kalendářního dne (jako sub cíl)."""
    s = get_setting(conn, "cgoal_session")
    if not s:
        s = now_iso()
        set_setting(conn, "cgoal_session", s)
    return s


def _ensure_session(conn) -> None:
    """Jen zajistí relaci. ŽÁDNÝ midnight reset – cíl nuluje výhradně reset() (konec streamu)."""
    _session(conn)


def reset(conn) -> None:
    """Konec streamu (live_events) → nová relace, vynuluj počítadlo i příznak výplaty. Každý stream
    začíná čistou lištou. JEDINÝ reset – přechod dne (půlnoc) cíl NEnuluje. Necommituje – commit caller."""
    set_setting(conn, "cgoal_session", now_iso())
    set_setting(conn, "cgoal_progress", "0")
    set_setting(conn, "cgoal_done", "0")


def status(conn) -> dict:
    """Stav cíle pro UI lištu (veřejné)."""
    _ensure_session(conn)
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
    _ensure_session(conn)
    conn.execute(
        "UPDATE app_settings SET value = CAST(COALESCE(value,'0') AS INTEGER) + 1, updated_at = ? "
        "WHERE key = 'cgoal_progress'", (now_iso(),))
    if _int(conn, "cgoal_progress", 0) >= cfg["target"]:
        _fire(conn, cfg)


def _announce_async(text: str) -> None:
    """Hláška do Kick chatu v BACKGROUND threadu s VLASTNÍM conn. Kick API je synchronní HTTP –
    v request threadu na sdíleném conn drží write lock a blokuje jediný worker → výpadek
    (stalo se 2026-06-13; predikce/autodrop to taky řeší threadem). Handler se vrátí hned."""
    import threading

    def _bg():
        try:
            from .db import get_conn
            from . import kickbot
            c = get_conn()
            try:
                kickbot.send_message(c, text, kind="system")
            finally:
                c.close()
        except Exception:
            pass
    threading.Thread(target=_bg, daemon=True).start()


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
    _announce_async(f"🎉 CHAT DAL DNEŠNÍ CÍL! {n} diváků si bere +{reward} sedláků. "
                    f"Dík, že to s náma žijete v chatu! 💬🌾")
