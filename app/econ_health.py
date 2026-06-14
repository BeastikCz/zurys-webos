"""Zdraví ekonomiky: kategorizace pohybů bodů (faucet / sink / transfer) + denní trend.

Čistá logika NAD points_logem – žádné zásahy do herních/ekonomických endpointů,
takže nízké riziko (jen čte). Slouží admin dashboardu „Zdraví ekonomiky":
  * faucet  = nově vytvořené body do oběhu (tlačí inflaci)
  * sink    = body z oběhu ven (deflace, zdravé)
  * transfer= přesun mezi diváky (net ~0: hry, predikce, dary, blackjack)

Kategorie se určují podle `reason` stringů, které píší jednotlivé moduly
(viz add_points / try_debit volání napříč appkou). Neznámý reason → „Ostatní".
"""
import sqlite3
from datetime import datetime, timezone, timedelta


# (key, emoji, label, kind, [substringy reason v lowercase]). Pořadí = priorita
# (predikce/blackjack PŘED hrami, ať „vklad/sázka" nepadne do špatné kategorie).
_RULES = [
    ("watch",       "📺", "Sledování",           "faucet",   ["sledování stream"]),
    ("chat",        "💬", "Chat",                "faucet",   ["aktivita v chatu", "komunitní chat cíl"]),
    ("topchat",     "🗣️", "Top chatteři",        "faucet",   ["top chatter"]),
    ("daily",       "📅", "Denní bonus",         "faucet",   ["denní streak"]),
    ("wheel",       "🎡", "Kolo štěstí",         "faucet",   ["kolo štěstí"]),
    ("drops",       "🎁", "Dropy",               "faucet",   ["drop #"]),
    ("codes",       "🎟️", "Redeem kódy",         "faucet",   ["redeem kód"]),
    ("quests",      "📋", "Úkoly",               "faucet",   ["úkol:"]),
    ("kick",        "💜", "Kick eventy",         "faucet",   ["kick sub", "kick resub", "kick gift sub", "kick follow"]),
    ("partners",    "🤝", "Partneři",            "faucet",   ["partner:", "flash partner"]),
    ("import",      "📦", "Import / start",      "faucet",   ["import ze staré", "počáteční body od admina"]),
    ("shop",        "🛒", "Nákupy v shopu",      "sink",     ["nákup odměn", "nákup"]),
    ("prestige",    "🔥", "Prestige (spáleno)",  "sink",     ["prestige"]),
    ("predictions", "🎯", "Predikce",            "transfer", ["predikce"]),
    ("blackjack",   "🃏", "Blackjack",           "transfer", ["blackjack"]),
    ("mines",       "💣", "Mines",               "transfer", ["mines"]),
    ("games",       "🎲", "Hry (PvP)",           "transfer", ["piškvor", "duel", "remíz", "coinflip",
                                                              "kostky", "kámen-nůžky", "hra #", "vklad"]),
    ("gifts",       "🎀", "Dary mezi diváky",    "transfer", ["dar pro", "dar od", "dar →", "vrácení daru"]),
]
_OTHER = ("other", "❓", "Ostatní / ruční", "other")


def categorize(reason: str):
    """(key, emoji, label, kind) pro daný reason. Neznámý → _OTHER."""
    r = (reason or "").lower()
    for key, emoji, label, kind, subs in _RULES:
        if any(s in r for s in subs):
            return key, emoji, label, kind
    return _OTHER


def health(conn: sqlite3.Connection, days: int = 14) -> dict:
    """Souhrn zdraví ekonomiky za posledních `days` dní (UTC).

    Vrací faucet/sink celkem + net + inflaci, rozpad podle kategorií, denní
    řadu (minted/burned/net/DAU/oběh) a špičku/průměr DAU.
    """
    try:
        days = max(1, min(90, int(days)))
    except (TypeError, ValueError):
        days = 14
    now = datetime.now(timezone.utc)
    win_start = (now - timedelta(days=days)).isoformat()

    # --- rozpad podle kategorií (group by reason → bucket dle categorize) ---
    cats: dict = {}
    for row in conn.execute(
        "SELECT reason, "
        "COALESCE(SUM(CASE WHEN change > 0 THEN change ELSE 0 END), 0) AS minted, "
        "COALESCE(SUM(CASE WHEN change < 0 THEN -change ELSE 0 END), 0) AS burned "
        "FROM points_log WHERE created_at >= ? AND change != 0 GROUP BY reason",
        (win_start,),
    ):
        key, emoji, label, kind = categorize(row["reason"])
        c = cats.setdefault(key, {"key": key, "emoji": emoji, "label": label,
                                  "kind": kind, "minted": 0, "burned": 0})
        c["minted"] += row["minted"]
        c["burned"] += row["burned"]
    by_category = []
    for c in cats.values():
        c["net"] = c["minted"] - c["burned"]
        by_category.append(c)
    by_category.sort(key=lambda c: abs(c["net"]), reverse=True)

    faucet_total = sum(c["minted"] for c in by_category)
    sink_total = sum(c["burned"] for c in by_category)
    net_total = faucet_total - sink_total

    # --- denní řada: minted / burned / DAU (aktivní = měl pohyb bodů) ---
    series_rows = conn.execute(
        "SELECT substr(created_at, 1, 10) AS d, "
        "COALESCE(SUM(CASE WHEN change > 0 THEN change ELSE 0 END), 0) AS minted, "
        "COALESCE(SUM(CASE WHEN change < 0 THEN -change ELSE 0 END), 0) AS burned, "
        "COUNT(DISTINCT user_id) AS dau "
        "FROM points_log WHERE created_at >= ? AND change != 0 GROUP BY d ORDER BY d",
        (win_start,),
    ).fetchall()

    circulation = conn.execute("SELECT COALESCE(SUM(points), 0) AS c FROM users").fetchone()["c"]
    # baseline oběhu před oknem → běžící součet dá oběh ke konci každého dne
    base = conn.execute(
        "SELECT COALESCE(SUM(change), 0) AS c FROM points_log WHERE created_at < ? AND change != 0",
        (win_start,),
    ).fetchone()["c"]
    series = []
    running = base
    for r in series_rows:
        net = r["minted"] - r["burned"]
        running += net
        series.append({"date": r["d"], "minted": r["minted"], "burned": r["burned"],
                       "net": net, "dau": r["dau"], "circulation": running})

    dau_vals = [s["dau"] for s in series]
    inflation_pct = round(net_total * 100.0 / circulation, 2) if circulation else 0.0
    active_users = conn.execute(
        "SELECT COUNT(DISTINCT user_id) AS c FROM points_log WHERE created_at >= ? AND change != 0",
        (win_start,),
    ).fetchone()["c"]

    return {
        "days": days,
        "circulation": circulation,
        "faucet_total": faucet_total,
        "sink_total": sink_total,
        "net_total": net_total,
        "inflation_pct": inflation_pct,
        "by_category": by_category,
        "series": series,
        "active_users": active_users,
        "dau_peak": max(dau_vals) if dau_vals else 0,
        "dau_avg": round(sum(dau_vals) / len(dau_vals)) if dau_vals else 0,
    }
