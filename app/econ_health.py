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
    ("kick",        "💜", "Kick eventy",         "faucet",   ["kick sub", "kick resub", "kick gift sub", "kick follow", "sub cíl"]),
    ("partners",    "🤝", "Partneři",            "faucet",   ["partner:", "flash partner"]),
    ("import",      "📦", "Import / start",      "faucet",   ["import ze staré", "počáteční body od admina"]),
    ("garden_h",    "🌾", "Zahrádka – sklizeň",  "faucet",   ["sklizeň:"]),
    ("shop",        "🛒", "Nákupy v shopu",      "sink",     ["nákup odměn", "nákup"]),
    ("garden_s",    "🌱", "Zahrádka – semínka",  "sink",     ["zasazení:"]),
    ("garden_d",    "🪴", "Zahrádka – dekorace", "sink",     ["dekorace zahrádky"]),
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


def normalized_reason(reason: str) -> dict:
    key, emoji, label, kind = categorize(reason)
    return {"key": key, "emoji": emoji, "label": label, "kind": kind}


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


_GARDEN_CROPS = [("Mrkev", "🥕"), ("Brambory", "🥔"), ("Dýně", "🎃"), ("Zlatý klas", "🌾")]


def garden_economy(conn: sqlite3.Connection) -> dict:
    """Ekonomika zahrádky z points_logu: výdaje (semínka + dekorace) vs příjmy (sklizně) a net.
    net > 0 = zahrádka přidává body do oběhu (faucet/inflační); net < 0 = ubírá (sink).
    Okna 24 h / 7 dní / celkem + rozpad podle plodin (celkem) + co teď roste. Read-only."""
    now = datetime.now(timezone.utc)
    windows = {"d1": (now - timedelta(hours=24)).isoformat(),
               "d7": (now - timedelta(days=7)).isoformat(),
               "all": "2000-01-01T00:00:00+00:00"}

    def _agg(start):
        def q(cond):
            r = conn.execute("SELECT COALESCE(SUM(ABS(change)), 0) AS n, COUNT(*) AS c "
                             "FROM points_log WHERE created_at >= ? AND " + cond, (start,)).fetchone()
            return r["n"], r["c"]
        seeds_n, seeds_c = q("change < 0 AND lower(reason) LIKE 'zasazení:%'")
        decor_n, decor_c = q("change < 0 AND lower(reason) LIKE 'dekorace zahrádky%'")
        harv_n, harv_c = q("change > 0 AND lower(reason) LIKE 'sklizeň:%'")
        return {"seeds": seeds_n, "seeds_count": seeds_c, "decor": decor_n, "decor_count": decor_c,
                "vydaje": seeds_n + decor_n, "prijmy": harv_n, "harvest_count": harv_c,
                "net": harv_n - (seeds_n + decor_n)}

    per_crop = []
    for name, icon in _GARDEN_CROPS:
        s = conn.execute("SELECT COALESCE(SUM(-change), 0) AS n, COUNT(*) AS c FROM points_log "
                         "WHERE change < 0 AND lower(reason) LIKE ?", (f"zasazení: {name.lower()}%",)).fetchone()
        h = conn.execute("SELECT COALESCE(SUM(change), 0) AS n, COUNT(*) AS c FROM points_log "
                         "WHERE change > 0 AND lower(reason) LIKE ?", (f"sklizeň: {name.lower()}%",)).fetchone()
        per_crop.append({"name": name, "icon": icon, "planted": s["c"], "seed_spent": s["n"],
                         "harvested": h["c"], "harvest_earned": h["n"], "net": h["n"] - s["n"]})

    from .garden import _BY_KEY as _gbk          # názvy/ikony plodin = jediný zdroj pravdy
    growing = []
    for r in conn.execute("SELECT crop, COUNT(*) AS c FROM garden GROUP BY crop ORDER BY c DESC"):
        cc = _gbk.get(r["crop"], {})
        growing.append({"crop": cc.get("name", r["crop"]), "icon": cc.get("icon", "🌱"), "count": r["c"]})

    per_user = [dict(r) for r in conn.execute(
        "SELECT u.id, u.username, "
        "COALESCE(SUM(CASE WHEN l.change > 0 THEN l.change ELSE 0 END), 0) AS gained, "
        "COALESCE(SUM(CASE WHEN l.change < 0 THEN -l.change ELSE 0 END), 0) AS spent, "
        "COALESCE(SUM(l.change), 0) AS net, COUNT(*) AS count "
        "FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.change != 0 AND (lower(l.reason) LIKE 'sklizeĹ:%' "
        "OR lower(l.reason) LIKE 'zasazenĂ­:%' "
        "OR lower(l.reason) LIKE 'zĂˇchrana:%' "
        "OR lower(l.reason) LIKE 'dekorace zahrĂˇdky%') "
        "GROUP BY l.user_id ORDER BY net DESC LIMIT 12")]

    recent = []
    for r in conn.execute(
        "SELECT u.username, l.change, l.reason, l.created_at "
        "FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.change != 0 AND (lower(l.reason) LIKE 'sklizeĹ:%' "
        "OR lower(l.reason) LIKE 'zasazenĂ­:%' "
        "OR lower(l.reason) LIKE 'zĂˇchrana:%' "
        "OR lower(l.reason) LIKE 'dekorace zahrĂˇdky%') "
        "ORDER BY l.id DESC LIMIT 12"):
        d = dict(r)
        d["category"] = normalized_reason(d["reason"])
        recent.append(d)

    return {"by_window": {k: _agg(v) for k, v in windows.items()},
            "per_crop": per_crop, "growing": growing,
            "per_user": per_user, "recent": recent}


def insights(conn: sqlite3.Connection, days: int = 1) -> dict:
    try:
        days = max(1, min(30, int(days)))
    except (TypeError, ValueError):
        days = 1
    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    from .deps import earn_factor

    users = {}
    for r in conn.execute(
        "SELECT u.id, u.username, l.change, l.reason "
        "FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.created_at >= ? AND l.change != 0", (start,)):
        d = users.setdefault(r["id"], {
            "id": r["id"], "username": r["username"],
            "farm_xp": 0, "farm_gross": 0,
            "gambling_in": 0, "gambling_out": 0,
            "garden_gained": 0, "garden_spent": 0,
        })
        reason = r["reason"] or ""
        change = r["change"]
        cat = categorize(reason)[0]
        if change > 0:
            factor = earn_factor(reason)
            if factor > 0:
                d["farm_gross"] += change
                d["farm_xp"] += int(round(change * factor))
            if factor == 0:
                d["gambling_in"] += change
            if cat == "garden_h":
                d["garden_gained"] += change
        else:
            spent = -change
            if cat in ("mines", "games", "blackjack", "predictions"):
                d["gambling_out"] += spent
            if cat in ("garden_s", "garden_d"):
                d["garden_spent"] += spent

    rows = list(users.values())
    for d in rows:
        d["gambling_net"] = d["gambling_in"] - d["gambling_out"]
        d["gambling_volume"] = d["gambling_in"] + d["gambling_out"]
        d["garden_net"] = d["garden_gained"] - d["garden_spent"]

    def top(key, pred):
        return sorted([x for x in rows if pred(x)], key=lambda x: x[key], reverse=True)[:10]

    return {
        "days": days,
        "top_farmers": top("farm_xp", lambda x: x["farm_xp"] > 0),
        "top_gamblers": top("gambling_volume", lambda x: x["gambling_volume"] > 0),
        "top_garden": top("garden_net", lambda x: x["garden_gained"] or x["garden_spent"]),
        "categories": [
            {"key": key, "emoji": emoji, "label": label, "kind": kind}
            for key, emoji, label, kind, _subs in _RULES
        ] + [{"key": _OTHER[0], "emoji": _OTHER[1], "label": _OTHER[2], "kind": _OTHER[3]}],
    }


def garden_economy(conn: sqlite3.Connection) -> dict:
    """Robust garden economy read model; uses normalized categories, not raw Czech SQL LIKE."""
    from .garden import CROPS, _BY_KEY, SEED_PCT, SEED_PCT_SUB

    now = datetime.now(timezone.utc)
    windows = {"d1": (now - timedelta(hours=24)).isoformat(),
               "d7": (now - timedelta(days=7)).isoformat(),
               "all": "2000-01-01T00:00:00+00:00"}
    out = {k: {"seeds": 0, "seeds_count": 0, "decor": 0, "decor_count": 0,
               "vydaje": 0, "prijmy": 0, "harvest_count": 0, "net": 0}
           for k in windows}
    crops = {c["name"].lower(): {"name": c["name"], "icon": c["icon"], "planted": 0,
                                 "seed_spent": 0, "harvested": 0, "harvest_earned": 0, "net": 0}
             for c in CROPS}
    users = {}
    recent = []
    garden_keys = {"garden_h", "garden_s", "garden_d"}

    for r in conn.execute(
        "SELECT u.id, u.username, l.change, l.reason, l.created_at "
        "FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.change != 0 ORDER BY l.id DESC"):
        cat = normalized_reason(r["reason"])
        key = cat["key"]
        if key not in garden_keys:
            continue
        change = r["change"]
        spent = -change if change < 0 else 0
        gained = change if change > 0 else 0
        for wkey, start in windows.items():
            if r["created_at"] >= start:
                w = out[wkey]
                if key == "garden_h":
                    w["prijmy"] += gained
                    w["harvest_count"] += 1
                elif key == "garden_s":
                    w["seeds"] += spent
                    w["seeds_count"] += 1
                elif key == "garden_d":
                    w["decor"] += spent
                    w["decor_count"] += 1
                w["vydaje"] = w["seeds"] + w["decor"]
                w["net"] = w["prijmy"] - w["vydaje"]

        text = (r["reason"] or "").lower()
        for crop_name, crop in crops.items():
            if crop_name in text:
                if key == "garden_h":
                    crop["harvested"] += 1
                    crop["harvest_earned"] += gained
                elif key == "garden_s":
                    crop["planted"] += 1
                    crop["seed_spent"] += spent
                crop["net"] = crop["harvest_earned"] - crop["seed_spent"]
                break

        u = users.setdefault(r["id"], {"id": r["id"], "username": r["username"],
                                       "gained": 0, "spent": 0, "net": 0, "count": 0})
        u["gained"] += gained
        u["spent"] += spent
        u["net"] += change
        u["count"] += 1
        if len(recent) < 12:
            recent.append({"username": r["username"], "change": change, "reason": r["reason"],
                           "created_at": r["created_at"], "category": cat})

    growing = []
    for r in conn.execute("SELECT crop, COUNT(*) AS c FROM garden GROUP BY crop ORDER BY c DESC"):
        cc = _BY_KEY.get(r["crop"], {})
        growing.append({"crop": cc.get("name", r["crop"]), "icon": cc.get("icon", "🌱"), "count": r["c"]})

    return {"by_window": out, "per_crop": list(crops.values()), "growing": growing,
            "per_user": sorted(users.values(), key=lambda x: x["net"], reverse=True)[:12],
            "recent": recent, "seed_pct": int(SEED_PCT * 100), "seed_pct_sub": int(SEED_PCT_SUB * 100)}
