"""Ekonomika pasivního výdělku: body za sledování + chat aktivitu, s násobičem pro SUB/VIP.

Násobič se aplikuje JEN na pasivní výdělek (sledování, chat) – ne na admin granty,
nákupy ani výhry z dropů. Vše je omezené denním stropem + cooldownem (anti-spam).
Nastavitelné v admin panelu (ukládá se do app_settings).
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import ROLE_SUB, ROLE_VIP, ROLE_ADMIN
from .db import now_iso, get_setting, set_setting, local_date
from .deps import add_points
from . import live

# Výchozí hodnoty (přepíše admin v UI). Odpovídají referenčnímu screenshotu.
DEFAULTS = {
    "eco_pts_per_min":    1,      # sedláci za 1 minutu sledování (základ pro Free)
    "eco_sub_mult":       5,      # násobič bodů pro SUB (×5)
    "eco_vip_mult":       2,      # násobič bodů pro VIP (×2 navrch → sub+VIP = ×10)
    "eco_chat_pts":       1,      # sedláci za aktivní zprávu v chatu
    "eco_chat_cooldown_s": 300,   # min. rozestup mezi odměnami za chat (s) = 5 min
    "eco_daily_cap":      5000,   # strop pasivního výdělku za den (sedláci)
    "eco_games_cap":      15000,  # denní strop ČISTÉHO zisku z her (coinflip/kostky/piškvorky); 0 = bez limitu
    "eco_watch_enabled":  1,      # body za sledování zapnuté
    "eco_chat_enabled":   1,      # body za chat zapnuté
    # Body za Kick eventy (přičítá webhook /api/kick/webhook – až bude napojený):
    "eco_sub_pts":        1000,   # nový sub
    "eco_resub_pts":      1000,   # resub
    "eco_giftsub_pts":    1000,   # za KAŽDÝ darovaný sub (5× gift = 5000)
    "eco_follow_pts":     100,    # follow (jednorázově)
}
WATCH_COOLDOWN_S = 295            # 1 odměna za sledování ≈ za 5 minut (anti-spam heartbeatů)


def get_eco(conn: sqlite3.Connection) -> dict:
    """Všechna ekonomická nastavení jako int (s fallbackem na DEFAULTS)."""
    out = {}
    for k, dv in DEFAULTS.items():
        raw = get_setting(conn, k, "")
        try:
            out[k] = int(raw) if raw != "" else dv
        except (ValueError, TypeError):
            out[k] = dv
    return out


def set_eco(conn: sqlite3.Connection, values: dict) -> dict:
    """Uloží zadané hodnoty (jen známé klíče, nezáporné). Vrátí aktuální stav."""
    for k, v in (values or {}).items():
        if k in DEFAULTS and v is not None:
            try:
                set_setting(conn, k, str(max(0, int(v))))
            except (ValueError, TypeError):
                pass
    conn.commit()
    return get_eco(conn)


def multiplier_for(user, eco: dict) -> int:
    """Kombinovaný násobič dle flagů: SUB ×eco_sub_mult, VIP ×eco_vip_mult.

    Násobí se (sub+VIP = obojí, např. ×5 × ×2 = ×10). Bere is_sub/is_vip flagy
    (nezávislé na roli – fungují i pro naimportované účty). Admin/staff role nehraje.
    """
    m = 1
    try:
        if user["is_sub"]:
            m *= max(1, eco["eco_sub_mult"])
        if user["is_vip"]:
            m *= max(1, eco["eco_vip_mult"])
        return m
    except (KeyError, IndexError, TypeError):
        # fallback na roli (kdyby chyběly flagy)
        if user["role"] == ROLE_SUB:
            return max(1, eco["eco_sub_mult"])
        if user["role"] == ROLE_VIP:
            return max(1, eco["eco_vip_mult"])
        return 1


def _today() -> str:
    return local_date()          # den podle českého času


def _state(conn: sqlite3.Connection, user_id: int):
    """Vrátí (a založí/resetuje) řádek activity_state pro dnešek."""
    row = conn.execute("SELECT * FROM activity_state WHERE user_id = ?", (user_id,)).fetchone()
    today = _today()
    if not row:
        conn.execute("INSERT INTO activity_state (user_id, day) VALUES (?, ?)", (user_id, today))
        return conn.execute("SELECT * FROM activity_state WHERE user_id = ?", (user_id,)).fetchone()
    if row["day"] != today:  # nový den → reset počítadel
        conn.execute(
            "UPDATE activity_state SET day = ?, earned_today = 0, watch_today = 0, chat_today = 0, "
            "games_net_today = 0 WHERE user_id = ?", (today, user_id))
        return conn.execute("SELECT * FROM activity_state WHERE user_id = ?", (user_id,)).fetchone()
    return row


def note_game_net(conn: sqlite3.Connection, user_id: int, delta: int) -> None:
    """Přičte čistý zisk/ztrátu z her do dnešního herního počítadla (pro denní strop grindu)."""
    _state(conn, user_id)                     # zajistí řádek + denní reset
    conn.execute("UPDATE activity_state SET games_net_today = games_net_today + ? WHERE user_id = ?",
                 (int(delta), user_id))


def games_capped(conn: sqlite3.Connection, user) -> bool:
    """True když hráč dnes dosáhl denního stropu ČISTÉHO zisku z her (admin nikdy)."""
    try:
        if user["role"] == ROLE_ADMIN:
            return False
    except (KeyError, IndexError, TypeError):
        pass
    cap = get_eco(conn).get("eco_games_cap", 0)
    if cap <= 0:
        return False
    return _state(conn, user["id"])["games_net_today"] >= cap


def _seconds_since(iso_ts: str) -> float:
    if not iso_ts:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_ts)).total_seconds()
    except (ValueError, TypeError):
        return 1e9


def award_earned(conn: sqlite3.Connection, user, base: int, reason: str, kind: str) -> dict:
    """Připíše pasivní body: base × násobič, omezeno denním stropem. Aktualizuje počítadla."""
    eco = get_eco(conn)
    mult = multiplier_for(user, eco)
    want = max(0, int(base)) * mult
    # Happy Hour (po startu streamu) – dočasný násobič navíc nad sub/VIP
    try:
        from . import live_events
        _hm = live_events.happy_mult(conn)
        if _hm > 1.0:
            want = int(round(want * _hm))
    except Exception:
        pass
    st = _state(conn, user["id"])
    remaining = max(0, eco["eco_daily_cap"] - st["earned_today"])
    amount = min(want, remaining)
    if amount <= 0:
        return {"awarded": 0, "mult": mult, "capped": True, "earned_today": st["earned_today"],
                "daily_cap": eco["eco_daily_cap"]}
    add_points(conn, user["id"], amount, reason)
    col = "watch_today" if kind == "watch" else ("chat_today" if kind == "chat" else "earned_today")
    conn.execute(
        f"UPDATE activity_state SET earned_today = earned_today + ?, {col} = {col} + ? WHERE user_id = ?"
        if kind in ("watch", "chat") else
        "UPDATE activity_state SET earned_today = earned_today + ? WHERE user_id = ?",
        ((amount, amount, user["id"]) if kind in ("watch", "chat") else (amount, user["id"])),
    )
    return {"awarded": amount, "mult": mult, "capped": amount < want,
            "earned_today": st["earned_today"] + amount, "daily_cap": eco["eco_daily_cap"]}


def award_watch(conn: sqlite3.Connection, user) -> dict:
    """Odměna za minutu sledování (volá se z heartbeatu). Cooldown ~1 min."""
    eco = get_eco(conn)
    if not eco["eco_watch_enabled"]:
        return {"awarded": 0, "disabled": True}
    # body za sledování JEN když stream běží (kick.com/<channel> live)
    if not live.is_live(conn):
        return {"awarded": 0, "offline": True}
    _state(conn, user["id"])   # zajisti řádek activity_state
    # Atomický claim slotu: připíše JEN když od poslední odměny uplynul cooldown. Podmíněná
    # UPDATE (SQLite serializuje zápisy) zabrání dvojímu přičtení při souběžných heartbeatech
    # (např. dva otevřené taby) – druhý request už uvidí nový last_watch_at a rowcount=0.
    threshold = (datetime.now(timezone.utc) - timedelta(seconds=WATCH_COOLDOWN_S)).isoformat()
    cur = conn.execute(
        "UPDATE activity_state SET last_watch_at = ? WHERE user_id = ? "
        "AND (last_watch_at IS NULL OR last_watch_at < ?)",
        (now_iso(), user["id"], threshold),
    )
    if cur.rowcount == 0:
        conn.commit()
        return {"awarded": 0, "cooldown": 1}
    res = award_earned(conn, user, eco["eco_pts_per_min"], "Sledování streamu", "watch")
    conn.commit()
    return res


def award_chat(conn: sqlite3.Connection, user) -> dict:
    """Odměna za aktivní zprávu v chatu. JEN když je stream LIVE. Cooldown dle nastavení."""
    eco = get_eco(conn)
    if not eco["eco_chat_enabled"]:
        return {"awarded": 0, "disabled": True}
    if not live.is_live(conn):                              # body za chat JEN když je stream live
        return {"awarded": 0, "offline": True}
    from .config import BOT_USERNAMES                       # boti neberou body za chat (ani neplní cíl)
    uname = (user["kick_username"] or user["username"] or "").strip().lower()
    if uname in BOT_USERNAMES:
        return {"awarded": 0, "bot": True}
    _state(conn, user["id"])
    # Atomický claim slotu (stejně jako u sledování) – brání dvojímu přičtení při souběhu.
    threshold = (datetime.now(timezone.utc) - timedelta(seconds=eco["eco_chat_cooldown_s"])).isoformat()
    cur = conn.execute(
        "UPDATE activity_state SET last_chat_at = ? WHERE user_id = ? "
        "AND (last_chat_at IS NULL OR last_chat_at < ?)",
        (now_iso(), user["id"], threshold),
    )
    if cur.rowcount == 0:
        conn.commit()
        return {"awarded": 0, "cooldown": 1}
    res = award_earned(conn, user, eco["eco_chat_pts"], "Aktivita v chatu", "chat")
    try:                                          # +1 do komunitního chat cíle (genuine zpráva po cooldownu)
        from . import community_goal
        community_goal.tick(conn)
    except Exception:
        pass
    conn.commit()
    return res


def award_chat_by_kick(conn: sqlite3.Connection, kick_username: str) -> dict:
    """Najde uživatele podle Kick nicku a odmění ho za chat aktivitu (pro reálné/demo čtení chatu)."""
    key = (kick_username or "").strip().lstrip("@").lower()
    if not key:
        return {"awarded": 0, "error": "no username"}
    user = conn.execute("SELECT * FROM users WHERE kick_username = ?", (key,)).fetchone()
    if not user:
        return {"awarded": 0, "error": "user not found", "kick_username": key}
    if user["banned"]:
        return {"awarded": 0, "error": "banned"}
    return award_chat(conn, user)


def activity_summary(conn: sqlite3.Connection, user) -> dict:
    """Přehled dnešního pasivního výdělku pro UI."""
    eco = get_eco(conn)
    st = _state(conn, user["id"])
    conn.commit()
    return {
        "mult": multiplier_for(user, eco),
        "earned_today": st["earned_today"],
        "watch_today": st["watch_today"],
        "chat_today": st["chat_today"],
        "daily_cap": eco["eco_daily_cap"],
        "pts_per_min": eco["eco_pts_per_min"],
        "chat_pts": eco["eco_chat_pts"],
        "watch_enabled": bool(eco["eco_watch_enabled"]),
        "chat_enabled": bool(eco["eco_chat_enabled"]),
        "live": live.is_live(conn),
        "live_mode": live.get_mode(conn),
        "role": user["role"],
    }
