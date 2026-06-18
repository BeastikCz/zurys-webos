"""Denní/týdenní úkoly (questy).

Postup se počítá DIFFEM celoživotního statu od začátku období (baseline) – tj.
o kolik stat narostl během dne/týdne. Decoupled: žádné zásahy do herních
endpointů (stejná filozofie jako achievements scanner). Server ověří splnění
i při claimu (nevěří klientovi).
"""
from datetime import datetime, timezone

from .db import now_iso, local_date, local_week_id
from .deps import add_points

# Hlavní vypínač úkolů. False = úkoly jsou MIMO PROVOZ: endpointy /quests vrátí prázdno,
# claim se zamítne a karta se na webu schová. Zpátky zapneš změnou na True + deploy.
QUESTS_ENABLED = True

# Odměny vyvážené proti inflaci (max ~550/den + ~3100/týд = ~6950/týд na tryharda).
# Watch 📺 drženo nejvýš (odměňuje reálné sledování = cíl streamera), duel nejníž
# (PvP je už placené stakem + nemotivovat collusion farmu, kterou hlídá funnel detektor).
# CÍLE (targets) zvednuté: týdenní mají zabrat celý týden (ne den), denní mají dávat
# poctivý poměr cena/výkon (víc práce za stejnou odměnu = míň inflace na úsilí).
QUESTS = [
    {"key": "d_drop", "period": "daily",  "name": "Lovec dropů",   "desc": "Chytni 3 dropy",                      "stat": "drops",   "target": 3,    "reward": 100},
    {"key": "d_duel", "period": "daily",  "name": "Vítěz dne",     "desc": "Vyhraj 5 PvP duelů",                  "stat": "pvp_won", "target": 5,    "reward": 100},
    {"key": "d_earn", "period": "daily",  "name": "Sedlák dříč",   "desc": "Vydělej 700 sedláků koukáním/chatem na streamu 📺", "stat": "earned",  "target": 700,  "reward": 150},
    {"key": "d_bet",  "period": "daily",  "name": "Sázkař",        "desc": "Podej 5 sázek v predikci",            "stat": "bets",    "target": 5,    "reward": 80},
    {"key": "d_chat", "period": "daily",  "name": "Ukecaný",       "desc": "Napiš 20× do chatu během streamu 💬", "stat": "chat",    "target": 20,   "reward": 120},
    {"key": "d_garden", "period": "daily", "name": "Zahradnik",     "desc": "Sklid 2 plodiny v zahradce",          "stat": "garden_harvest", "target": 2, "reward": 80},
    {"key": "d_shop", "period": "daily",   "name": "Mecenas dne",   "desc": "Utrat 1000 sedlaku v shopu",          "stat": "shop_spent", "target": 1000, "reward": 120},
    {"key": "w_drop", "period": "weekly", "name": "Týdenní lovec", "desc": "Chytni 60 dropů za týden",            "stat": "drops",   "target": 60,   "reward": 700},
    {"key": "w_duel", "period": "weekly", "name": "Gladiátor",     "desc": "Vyhraj 40 PvP duelů za týden",        "stat": "pvp_won", "target": 40,   "reward": 700},
    {"key": "w_earn", "period": "weekly", "name": "Magnát",        "desc": "Vydělej 12 000 sedláků na streamu za týden 📺", "stat": "earned",  "target": 12000, "reward": 900},
    {"key": "w_chat", "period": "weekly", "name": "Tlachal týdne", "desc": "Buď ukecaný celý týден — 350 zpráv 💬", "stat": "chat",    "target": 350,  "reward": 800},
]
_BY_KEY = {q["key"]: q for q in QUESTS}


def _period_id(period: str) -> str:
    """Identifikátor období podle ČESKÉHO času: daily → 'YYYY-MM-DD', weekly → 'YYYY-Www'."""
    return local_week_id() if period == "weekly" else local_date()


def _user_stat(conn, uid: int, stat: str) -> int:
    """Celoživotní hodnota statu uživatele (jen rostoucí → diff = postup za období)."""
    if stat == "drops":
        return conn.execute("SELECT COUNT(*) c FROM drop_claims WHERE user_id=?", (uid,)).fetchone()["c"]
    if stat == "bets":
        return conn.execute("SELECT COUNT(*) c FROM prediction_bets WHERE user_id=?", (uid,)).fetchone()["c"]
    if stat == "earned":
        # JEN výdělek ze streamu zurys1337 → body za sledování + chat (ty se přičítají jen když je live).
        # Admin granty, dárky, výhry v hrách ani odměny za questy/odznaky se NEpočítají –
        # jinak by se „Vydělej …" plnil sám i offline (např. vyzvednutím jiného úkolu).
        return conn.execute(
            "SELECT COALESCE(SUM(change),0) c FROM points_log "
            "WHERE user_id=? AND change>0 AND reason IN ('Sledování streamu','Aktivita v chatu')",
            (uid,)).fetchone()["c"]
    if stat == "chat":
        return conn.execute("SELECT COALESCE(SUM(change),0) c FROM points_log WHERE user_id=? AND change>0 AND reason='Aktivita v chatu'", (uid,)).fetchone()["c"]
    if stat == "garden_harvest":
        return conn.execute(
            "SELECT COUNT(*) c FROM points_log WHERE user_id=? AND change>0 AND reason LIKE 'Skliz%'",
            (uid,)).fetchone()["c"]
    if stat == "shop_spent":
        return conn.execute("SELECT COALESCE(SUM(points_spent),0) c FROM orders WHERE user_id=?", (uid,)).fetchone()["c"]
    if stat == "pvp_won":
        won = 0
        for tbl in ("duels", "games"):
            won += conn.execute(
                f"SELECT COUNT(*) c FROM {tbl} WHERE status='finished' AND winner IN (1,2) "
                f"AND ((winner=1 AND p1_id=?) OR (winner=2 AND p2_id=?))", (uid, uid)).fetchone()["c"]
        return won
    return 0


def _baseline(conn, uid: int, q: dict):
    """(baseline, claimed) pro aktuální období; při prvním přístupu řádek založí."""
    pid = _period_id(q["period"])
    row = conn.execute(
        "SELECT baseline, claimed FROM quest_progress WHERE user_id=? AND quest_key=? AND period_id=?",
        (uid, q["key"], pid)).fetchone()
    if row is None:
        cur = _user_stat(conn, uid, q["stat"])
        conn.execute(
            "INSERT INTO quest_progress (user_id, quest_key, period_id, baseline, claimed, created_at) "
            "VALUES (?,?,?,?,0,?) ON CONFLICT(user_id, quest_key, period_id) DO NOTHING",
            (uid, q["key"], pid, cur, now_iso()))
        conn.commit()
        return cur, 0
    return row["baseline"], row["claimed"]


def get_quests(conn, uid: int) -> list:
    """Seznam úkolů s postupem a stavem pro daného uživatele."""
    out = []
    for q in QUESTS:
        baseline, claimed = _baseline(conn, uid, q)
        progress = max(0, _user_stat(conn, uid, q["stat"]) - baseline)
        out.append({
            "key": q["key"], "period": q["period"], "name": q["name"], "desc": q["desc"],
            "target": q["target"], "reward": q["reward"],
            "progress": min(progress, q["target"]),
            "completed": progress >= q["target"], "claimed": bool(claimed),
        })
    return out


def claim_quest(conn, uid: int, key: str) -> dict:
    """Vyzvedne odměnu za SPLNĚNÝ a dosud nevyzvednutý úkol. Server ověří splnění."""
    q = _BY_KEY.get(key)
    if not q:
        raise ValueError("Neznámý úkol.")
    pid = _period_id(q["period"])
    baseline, claimed = _baseline(conn, uid, q)
    if claimed:
        raise ValueError("Tenhle úkol už máš vyzvednutý. 🎁")
    if _user_stat(conn, uid, q["stat"]) - baseline < q["target"]:
        raise ValueError("Úkol ještě není splněný. 💪")
    add_points(conn, uid, q["reward"], f"Úkol: {q['name']} 📋")
    conn.execute("UPDATE quest_progress SET claimed=1 WHERE user_id=? AND quest_key=? AND period_id=?",
                 (uid, key, pid))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    return {"ok": True, "balance": bal, "reward": q["reward"],
            "message": f"📋 Úkol splněn! +{q['reward']} sedláků 🌾"}
