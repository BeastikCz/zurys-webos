"""Komunitní SUB cíl: ESKALUJÍCÍ žebříček. Společná lišta se plní z Kick subů (sub/resub = +1,
gift sub = +n). Po každém TIERU (milníku po `step` subech) dostane každý dosud nevyplacený GIFTER
odměnu ve výši aktuálního tieru (tier × reward_step) – a cíl se zvedne na další tier. Tier 1 = step
subů → reward_step, tier 2 = 2×step → 2×reward_step, … až do `tier_max` (strop, dál cíl neroste).

Každý gifter dostane odměnu PRÁVĚ JEDNOU (paid flag) – ve výši tieru, ve kterém se vyplácí → kdo
giftne v pozdějším (vyšším) tieru, bere víc. NENÍ vázané na happy hour (odměnu bere každý gifter
sub cíle). Reset = konec streamu (live_events) nebo nový den. Stav/konfig v app_settings, seznam
dnešních gifterů + jejich paid stav v tabulce subgoal_gifters.

Flywheel: giftni suby → naplň tier → gifteři berou (eskalující) odměnu → motivace giftovat dál,
ať se odemkne vyšší tier s vyšší odměnou. Sourozenec community_goal.py (chat cíl).
"""
from .db import now_iso, get_setting, set_setting, local_date

DEFAULT_STEP = 10        # KROK: o kolik subů se posune cíl každý tier (setting subgoal_target)
DEFAULT_REWARD = 1000    # odměna za 1 tier; gifter ji dostane 1× ve výši svého tieru (setting subgoal_reward)
DEFAULT_TIER_MAX = 10    # strop: po tomhle tieru už cíl neroste (chrání ekonomiku) (setting subgoal_tier_max)


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
        "step": max(1, _int(conn, "subgoal_target", DEFAULT_STEP)),            # subů na 1 tier
        "reward_step": max(0, _int(conn, "subgoal_reward", DEFAULT_REWARD)),   # sedláků za 1 tier
        "tier_max": max(1, _int(conn, "subgoal_tier_max", DEFAULT_TIER_MAX)),  # strop tierů
    }


def _reached_tier(progress: int, cfg: dict) -> int:
    """Kolik tierů (milníků) je hotových při daném progressu, stropnuto na tier_max."""
    return min(progress // cfg["step"], cfg["tier_max"])


def _ensure_day(conn) -> None:
    """Nový den → vynuluj počítadlo, tier, příznak stropu i seznam dnešních gifterů."""
    if get_setting(conn, "subgoal_day") != _today():
        set_setting(conn, "subgoal_day", _today())
        set_setting(conn, "subgoal_progress", "0")
        set_setting(conn, "subgoal_tier", "0")
        set_setting(conn, "subgoal_done", "0")
        conn.execute("DELETE FROM subgoal_gifters WHERE day != ?", (_today(),))


def reset(conn) -> None:
    """Plný reset SUB cíle: počítadlo, tier, příznak stropu i seznam gifterů na nulu. Volá se na KONCI
    streamu (live_events), ať každý stream začíná na tieru 1. Necommituje – commit dělá caller."""
    set_setting(conn, "subgoal_progress", "0")
    set_setting(conn, "subgoal_tier", "0")
    set_setting(conn, "subgoal_done", "0")
    conn.execute("DELETE FROM subgoal_gifters")


def status(conn) -> dict:
    """Stav cíle pro UI lištu (veřejné). target/reward = DALŠÍ tier (kam lišta míří)."""
    _ensure_day(conn)
    cfg = _cfg(conn)
    progress = _int(conn, "subgoal_progress", 0)
    tier = _reached_tier(progress, cfg)
    maxed = tier >= cfg["tier_max"]
    next_tier = tier if maxed else tier + 1
    target = next_tier * cfg["step"]                       # absolutní cíl dalšího milníku
    reward = next_tier * cfg["reward_step"]                # odměna za příští tier
    gifters = conn.execute(
        "SELECT COUNT(*) c FROM subgoal_gifters WHERE day = ?", (_today(),)
    ).fetchone()["c"]
    conn.commit()
    return {
        "enabled": bool(cfg["enabled"]),
        "progress": min(progress, target),
        "target": target,
        "reward": reward,
        "tier": tier,                  # kolik tierů už hotových
        "tier_max": cfg["tier_max"],
        "maxed": maxed,
        "done": maxed,                 # „done" (zlatý stav overlaye) = dosažen strop tierů
        "gifters": gifters,            # kolik lidí dnes giftlo (engagement)
        "pct": min(100, round(progress * 100 / target)) if target else 0,
        # RAW konfig (pro admin formulář – edituje se KROK a odměna ZA TIER, ne computed milník):
        "step": cfg["step"],
        "reward_step": cfg["reward_step"],
    }


def tick(conn, count: int = 1) -> None:
    """+count subů do lišty. Po každém ticku zkusí vyplatit dosažené tiery (eskalující žebříček).
    Necommituje increment (commituje caller); _settle si commit dělá sám."""
    if count <= 0:
        return
    cfg = _cfg(conn)
    if not cfg["enabled"]:
        return
    _ensure_day(conn)
    conn.execute(
        "UPDATE app_settings SET value = CAST(COALESCE(value,'0') AS INTEGER) + ?, updated_at = ? "
        "WHERE key = 'subgoal_progress'", (count, now_iso()))
    _settle(conn, cfg)


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


def _settle(conn, cfg) -> None:
    """Eskalující výplata. Při dosaženém tieru (progress // step, stropnuto tier_max) vyplať KAŽDÉMU
    dosud nevyplacenému gifterovi (paid=0) odměnu ve výši AKTUÁLNÍHO tieru (tier × reward_step) a označ
    paid=1. Každý gifter tak bere právě jednou – ve výši tieru, ve kterém se vyplácí (kdo giftne výš,
    bere víc). Volá se z ticku po každém gift eventu. Oznámení do chatu při dosažení NOVÉHO tieru."""
    today = _today()
    progress = _int(conn, "subgoal_progress", 0)
    tier = _reached_tier(progress, cfg)
    if tier < 1:
        return                                       # ještě ani první milník
    reward = tier * cfg["reward_step"]
    eligible = [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM subgoal_gifters WHERE day = ? AND paid = 0", (today,)).fetchall()]
    newly = []
    for uid in eligible:                             # atomicky zaber každého → anti-double-pay i při souběhu
        if conn.execute("UPDATE subgoal_gifters SET paid = 1 WHERE day = ? AND user_id = ? AND paid = 0",
                        (today, uid)).rowcount == 1:
            newly.append(uid)
    if newly and reward > 0:
        qm = ",".join("?" * len(newly))
        conn.execute(f"UPDATE users SET points = points + ? WHERE id IN ({qm})", [reward, *newly])
        conn.executemany(
            "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
            [(uid, reward, f"Sub cíl tier {tier} 🟣🎁", now_iso()) for uid in newly])
        from .deps import notify
        for uid in newly:
            notify(conn, uid, "🟣", f"Sub cíl – tier {tier}!",
                   f"Komunita dosáhla tier {tier} – bereš +{reward} sedláků za gift sub! 🎁", "#/profile")
    # oznámení do chatu při dosažení NOVÉHO tieru (1× na tier)
    stored = _int(conn, "subgoal_tier", 0)
    leveled = tier > stored
    if leveled:
        set_setting(conn, "subgoal_tier", str(tier))
        set_setting(conn, "subgoal_done", "1" if tier >= cfg["tier_max"] else "0")
    conn.commit()
    if leveled:
        nxt = "MAX 🏆" if tier >= cfg["tier_max"] else f"{(tier + 1) * cfg['step']} subů"
        if newly:
            who = "gifter" if len(newly) == 1 else "gifterů"
            _announce_async(f"🟣 SUB CÍL — TIER {tier}! {len(newly)} {who} bere +{reward} sedláků za gift suby! "
                            f"Další tier = {nxt}. 🎁🌾")
        else:
            _announce_async(f"🟣 SUB CÍL — TIER {tier} odemčen! Kdo teď giftne sub, bere +{reward} sedláků. "
                            f"Další tier = {nxt}. 🎁")
