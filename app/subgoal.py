"""Komunitní SUB cíl: ESKALUJÍCÍ žebříček. Společná lišta se plní z Kick subů (sub/resub = +1,
gift sub = +n). Po každém TIERU (milníku po `step` subech) dostane každý dosud nevyplacený GIFTER
odměnu ve výši aktuálního tieru (tier × reward_step) – a cíl se zvedne na další tier. Tier 1 = step
subů → reward_step, tier 2 = 2×step → 2×reward_step, … `tier_max` = strop tierů, NEBO 0 = NEKONEČNO
(cíl roste donekonečna po `step`, nikdy „max tier", odměna za tier eskaluje dál).

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
DEFAULT_TIER_MAX = 0     # strop tierů; 0 = NEKONEČNO (cíl roste donekonečna po `step`) (setting subgoal_tier_max)


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
        "tier_max": _int(conn, "subgoal_tier_max", DEFAULT_TIER_MAX),          # strop tierů; ≤0 = nekonečno
    }


def _unlimited(cfg: dict) -> bool:
    return cfg["tier_max"] <= 0          # 0/≤0 = nekonečný žebříček (cíl roste donekonečna po `step`)


def _maxed(tier: int, cfg: dict) -> bool:
    return not _unlimited(cfg) and tier >= cfg["tier_max"]


def _reached_tier(progress: int, cfg: dict) -> int:
    """Kolik tierů (milníků) hotových při daném progressu; stropnuto na tier_max (0 = bez stropu)."""
    t = progress // cfg["step"]
    return t if _unlimited(cfg) else min(t, cfg["tier_max"])


def _session(conn) -> str:
    """Stabilní klíč RELACE sub cíle – mění se JEN na reset() (konec streamu), NE o půlnoci. Gifteři
    jsou keyovaní tímhle (ne kalendářním dnem _today), aby stream PŘES PŮLNOC držel jeden cíl. Cíl
    se tak nuluje výhradně koncem streamu, ne přechodem dne."""
    s = get_setting(conn, "subgoal_session")
    if not s:
        s = now_iso()
        set_setting(conn, "subgoal_session", s)
    return s


def _ensure_session(conn) -> None:
    """Jen zajistí, že relace existuje. ŽÁDNÝ midnight reset – cíl nuluje výhradně reset() (konec streamu)."""
    _session(conn)


def reset(conn) -> None:
    """Plný reset SUB cíle na KONCI streamu (live_events): NOVÁ relace (staří gifteři přestávají platit),
    počítadlo, tier, příznak stropu i seznam gifterů na nulu. Každý stream začíná na tieru 1. Tohle je
    JEDINÝ reset – přechod kalendářního dne (půlnoc) cíl NEnuluje. Necommituje – commit dělá caller."""
    set_setting(conn, "subgoal_session", now_iso())   # nová relace → DELETE gifterů níž je tím i čistý start
    set_setting(conn, "subgoal_progress", "0")
    set_setting(conn, "subgoal_tier", "0")
    set_setting(conn, "subgoal_done", "0")
    conn.execute("DELETE FROM subgoal_gifters")


def status(conn) -> dict:
    """Stav cíle pro UI lištu (veřejné). target/reward = DALŠÍ tier (kam lišta míří)."""
    _ensure_session(conn)
    cfg = _cfg(conn)
    progress = _int(conn, "subgoal_progress", 0)
    tier = _reached_tier(progress, cfg)
    maxed = _maxed(tier, cfg)
    next_tier = tier if maxed else tier + 1
    target = next_tier * cfg["step"]                       # absolutní cíl dalšího milníku
    reward = next_tier * cfg["reward_step"]                # odměna za příští tier
    gifters = conn.execute(
        "SELECT COUNT(*) c FROM subgoal_gifters WHERE day = ?", (_session(conn),)
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


def top_gifters(conn, limit: int = 5) -> list:
    """Top gifteři DNES (dle ČESKÉHO dne, z points_log gift sub eventů). Přesné (webhook real-time),
    resetuje se každou půlnoc → čerstvá soutěž. (Lifetime nejde – Kick blokuje fetch + app má gap z
    gift subů před spuštěním; nedávné okno ale app zachycuje přesně.)"""
    import re
    from datetime import datetime, timezone
    from .db import local_now, LOCAL_TZ
    n = local_now()
    start = datetime(n.year, n.month, n.day, tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()
    tot = {}
    for r in conn.execute(
            "SELECT user_id, reason FROM points_log WHERE created_at >= ? "
            "AND LOWER(reason) LIKE '%kick gift sub%' AND LOWER(reason) NOT LIKE '%příjemce%'", (start,)):
        m = re.search(r"×(\d+)", r["reason"])
        tot[r["user_id"]] = tot.get(r["user_id"], 0) + (int(m.group(1)) if m else 1)
    out = []
    for uid, subs in sorted(tot.items(), key=lambda x: -x[1])[:limit]:
        u = conn.execute("SELECT username, avatar_url FROM users WHERE id = ?", (uid,)).fetchone()
        if u:
            out.append({"username": u["username"], "avatar_url": u["avatar_url"], "subs": subs})
    return out


def latest_gift(conn):
    """Nejnovější gift sub event (pro stream alert overlay – sleduje změnu id = 'právě někdo giftnul')."""
    import re
    r = conn.execute(
        "SELECT pl.id, pl.reason, u.username, u.avatar_url FROM points_log pl JOIN users u ON u.id = pl.user_id "
        "WHERE LOWER(pl.reason) LIKE '%kick gift sub%' AND LOWER(pl.reason) NOT LIKE '%příjemce%' "
        "ORDER BY pl.id DESC LIMIT 1").fetchone()
    if not r:
        return None
    m = re.search(r"×(\d+)", r["reason"])
    return {"id": r["id"], "username": r["username"], "avatar_url": r["avatar_url"], "count": int(m.group(1)) if m else 1}


def recent_gifts(conn, since=None, limit: int = 20) -> dict:
    """Gift sub eventy s points_log.id > since (pro jednorázový alert overlay). since=None → jen vrátí
    aktuální latest_id (baseline, NEhlásí staré gifty). Filtr na id (PK, indexované) → rychlé i při pollingu.
    Vrací {latest_id, gifts:[{id,username,avatar_url,subs}]}."""
    import re
    overall = conn.execute("SELECT MAX(id) m FROM points_log").fetchone()
    latest = (overall["m"] if overall else 0) or 0
    if since is None:
        return {"latest_id": latest, "gifts": []}
    rows = conn.execute(
        "SELECT pl.id, pl.reason, u.username, u.avatar_url FROM points_log pl JOIN users u ON u.id = pl.user_id "
        "WHERE pl.id > ? AND LOWER(pl.reason) LIKE '%kick gift sub%' AND LOWER(pl.reason) NOT LIKE '%příjemce%' "
        "ORDER BY pl.id ASC LIMIT ?", (since, limit)).fetchall()
    gifts = []
    for r in rows:
        m = re.search(r"×(\d+)", r["reason"])
        gifts.append({"id": r["id"], "username": r["username"], "avatar_url": r["avatar_url"],
                      "subs": int(m.group(1)) if m else 1})
    return {"latest_id": latest, "gifts": gifts}


def recent_events(conn, since=None, limit: int = 20) -> dict:
    """Nové sub-typ eventy (new/resub/gift) s points_log.id > since pro sjednocený alert overlay.
    since=None → baseline (jen latest_id, staré nehlásí). Vrací {latest_id, events:[{id,kind,count,username,avatar_url}]}.
    kind: 'gift' (×N), 'resub', 'new'. Filtr na id (PK) → rychlé při pollingu."""
    import re
    overall = conn.execute("SELECT MAX(id) m FROM points_log").fetchone()
    latest = (overall["m"] if overall else 0) or 0
    if since is None:
        return {"latest_id": latest, "events": []}
    rows = conn.execute(
        "SELECT pl.id, pl.reason, u.username, u.avatar_url FROM points_log pl JOIN users u ON u.id = pl.user_id "
        "WHERE pl.id > ? AND LOWER(pl.reason) NOT LIKE '%příjemce%' AND ("
        "LOWER(pl.reason) LIKE '%kick gift sub%' OR LOWER(pl.reason) LIKE '%kick resub%' "
        "OR LOWER(pl.reason) LIKE '%kick sub%') ORDER BY pl.id ASC LIMIT ?", (since, limit)).fetchall()
    out = []
    for r in rows:
        rl = (r["reason"] or "").lower()
        base = {"id": r["id"], "username": r["username"], "avatar_url": r["avatar_url"]}
        if "kick gift sub" in rl:
            m = re.search(r"×(\d+)", r["reason"])
            out.append(dict(base, kind="gift", count=int(m.group(1)) if m else 1))
        elif "kick resub" in rl:
            out.append(dict(base, kind="resub", count=1))
        elif "kick sub" in rl:
            out.append(dict(base, kind="new", count=1))
    return {"latest_id": latest, "events": out}


def tick(conn, count: int = 1) -> None:
    """+count subů do lišty. Po každém ticku zkusí vyplatit dosažené tiery (eskalující žebříček).
    Necommituje increment (commituje caller); _settle si commit dělá sám."""
    if count <= 0:
        return
    cfg = _cfg(conn)
    if not cfg["enabled"]:
        return
    _ensure_session(conn)
    conn.execute(
        "UPDATE app_settings SET value = CAST(COALESCE(value,'0') AS INTEGER) + ?, updated_at = ? "
        "WHERE key = 'subgoal_progress'", (count, now_iso()))
    _settle(conn, cfg)


def record_gifter(conn, user_id: int, n: int, in_hh: bool) -> None:
    """Zaznamenej dnešního giftera. NOVÝ gifter dostane jako baseline AKTUÁLNÍ dosažený tier
    (paid_tier = už hotové tiery) → ty zpětně NEdostane, bere až tiery od svého příchodu výš.
    Volá kickevents při gift sub eventu PŘED tickem. Necommituje – commit dělá caller / _settle."""
    if not user_id or n <= 0:
        return
    _ensure_session(conn)
    cur_tier = _reached_tier(_int(conn, "subgoal_progress", 0), _cfg(conn))   # tiery už hotové = nedostane zpětně
    hh = n if in_hh else 0
    conn.execute(
        "INSERT INTO subgoal_gifters (day, user_id, subs, hh_subs, paid_tier) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(day, user_id) DO UPDATE SET subs = subs + ?, hh_subs = hh_subs + ?",
        (_session(conn), user_id, n, hh, cur_tier, n, hh))


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
    """KUMULATIVNÍ výplata. Při dosaženém tieru T vyplať každému gifterovi VŠECHNY tiery, které ještě
    nemá (od paid_tier+1 do T) – odměna za tier k = k × reward_step. EARLY gifteři tak berou každý další
    tier znova (1k + 2k + 3k…), POZDNÍ jen od svého příchodu (record_gifter jim dá baseline paid_tier =
    tehdy hotové tiery). Atomický claim paid_tier → anti-double-pay. Oznámení při NOVÉM tieru."""
    session = _session(conn)
    progress = _int(conn, "subgoal_progress", 0)
    tier = _reached_tier(progress, cfg)
    if tier < 1:
        return                                       # ještě ani první milník
    rs = cfg["reward_step"]
    rows = conn.execute(
        "SELECT user_id, paid_tier FROM subgoal_gifters WHERE day = ? AND paid_tier < ?", (session, tier)).fetchall()
    newly = []                                       # (uid, amount)
    for r in rows:
        uid, p = r["user_id"], r["paid_tier"] or 0
        amount = rs * sum(range(p + 1, tier + 1))    # součet tierů p+1..T (kumulativně)
        if amount > 0 and conn.execute(              # atomicky zaber (paid_tier p → T) – anti-double-pay
                "UPDATE subgoal_gifters SET paid_tier = ? WHERE day = ? AND user_id = ? AND paid_tier = ?",
                (tier, session, uid, p)).rowcount == 1:
            conn.execute("UPDATE users SET points = points + ? WHERE id = ?", (amount, uid))
            conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
                         (uid, amount, f"Sub cíl tier {tier} 🟣🎁", now_iso()))
            newly.append((uid, amount))
    if newly:
        from .deps import notify
        for uid, amt in newly:
            notify(conn, uid, "🟣", f"Sub cíl – tier {tier}!",
                   f"Komunita dosáhla tier {tier} – bereš +{amt} sedláků! 🎁", "#/profile")
    # oznámení do chatu při dosažení NOVÉHO tieru (1× na tier)
    stored = _int(conn, "subgoal_tier", 0)
    leveled = tier > stored
    if leveled:
        set_setting(conn, "subgoal_tier", str(tier))
        set_setting(conn, "subgoal_done", "1" if _maxed(tier, cfg) else "0")
    conn.commit()
    if leveled:
        nxt = "MAX 🏆" if _maxed(tier, cfg) else f"{(tier + 1) * cfg['step']} subů"
        tier_rw = tier * rs
        if newly:
            who = "gifter" if len(newly) == 1 else "gifterů"
            _announce_async(f"🟣 SUB CÍL — TIER {tier}! {len(newly)} {who} odměněno (+{tier_rw} za tier, věrní berou i předchozí kumulativně)! "
                            f"Další tier = {nxt}. 🎁🌾")
        else:
            _announce_async(f"🟣 SUB CÍL — TIER {tier} odemčen! Kdo teď giftne, bere +{tier_rw}. Další tier = {nxt}. 🎁")
