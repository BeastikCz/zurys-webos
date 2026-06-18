"""Zahrádka (farm-sim): sázej plodiny → rostou v reálném čase → sklidíš sedláky.

Loop: zaplať sazbu → plodina dorůstá `hours` hodin → harvest = odměna (zisk). Gated
časem + počtem záhonů (anti-inflace; sázení navíc reinvestuje sedláky zpět). Plně
na téma sedláka. Prázdný záhon = žádný řádek v `garden`.
"""
import random
import sqlite3
from datetime import datetime, timezone, timedelta

from .db import now_iso

N_PLOTS = 4
SEED_PCT = 0.75       # cena semínka = 75 % výnosu; zahrádka je fun bonus, ne hlavní faucet
SEED_PCT_SUB = 0.65   # sub perk: lepší marže, ale pořád bez tisku sedláků
PEST_CHANCE = 0.70    # base pest chance; garden decor reduces this down to PEST_MIN_CHANCE
PEST_RESCUE_PCT = 0.20  # záchrana před chrobáci = 20 % výnosu (sink). Nezachráníš → sklizeň jen poloviční.
PEST_PENALTY = 0.5    # neošetření chrobáci sežerou půlku úrody
PEST_MIN_CHANCE = 0.10   # dolní strop šance i s plnou výbavou dekorací

# (key, ikona, název, hodiny růstu, výnos při sklizni). Cena semínka = % z výnosu (počítá se dle subu).
CROPS = [
    {"key": "mrkev",    "icon": "🥕", "name": "Mrkev",       "hours": 1,  "reward": 50},
    {"key": "brambory", "icon": "🥔", "name": "Brambory",    "hours": 3,  "reward": 150},
    {"key": "dyne",     "icon": "🎃", "name": "Dýně",        "hours": 12, "reward": 600},
    {"key": "klas",     "icon": "🌾", "name": "Zlatý klas",  "hours": 24, "reward": 1400},
]
_BY_KEY = {c["key"]: c for c in CROPS}

DECOR_PEST_REDUCTION = {   # součet = 60 pb → plná výbava sráží 70 % na 10 % (= PEST_MIN_CHANCE)
    "sunflower": 0.02,
    "tree": 0.03,
    "well": 0.04,
    "tractor": 0.05,
    "house": 0.06,
    "rainbow": 0.07,
    "scarecrow": 0.09,
    "fountain": 0.11,
    "manor": 0.13,
}


def _is_sub(user) -> bool:
    try:
        return bool(user["is_sub"]) or user["role"] == "admin"
    except (KeyError, IndexError, TypeError):
        return False


def _seed_cost(crop, is_sub: bool) -> int:
    """Cena semínka = % výnosu (sub má poloviční). Min 1."""
    return max(1, round(crop["reward"] * (SEED_PCT_SUB if is_sub else SEED_PCT)))


def _rescue_cost(crop) -> int:
    """Cena záchrany před chrobáci = % výnosu. Min 5."""
    return max(5, round(crop["reward"] * PEST_RESCUE_PCT))


def _pest_chance(conn, user_id: int) -> float:
    owned = [r["decor_key"] for r in conn.execute(
        "SELECT decor_key FROM garden_decor WHERE user_id = ?", (user_id,))]
    reduction = sum(DECOR_PEST_REDUCTION.get(k, 0.0) for k in owned)
    return max(PEST_MIN_CHANCE, PEST_CHANCE - reduction)


def _crops_public(is_sub: bool, pest_chance: float) -> list:
    out = []
    for c in CROPS:
        seed = _seed_cost(c, is_sub)
        expected_no_rescue = round(c["reward"] * ((1 - pest_chance) + pest_chance * PEST_PENALTY) - seed)
        expected_rescue = round(c["reward"] - seed - (_rescue_cost(c) * pest_chance))
        out.append({"key": c["key"], "icon": c["icon"], "name": c["name"], "hours": c["hours"],
                    "reward": c["reward"], "cost": seed, "net": c["reward"] - seed,
                    "expected_no_rescue": expected_no_rescue,
                    "expected_rescue": expected_rescue})
    return out


def status(conn, user) -> dict:
    now = datetime.now(timezone.utc)
    pest_chance = _pest_chance(conn, user["id"])
    rows = {r["plot"]: r for r in conn.execute(
        "SELECT * FROM garden WHERE user_id = ?", (user["id"],))}
    plots = []
    for p in range(N_PLOTS):
        r = rows.get(p)
        if not r:
            plots.append({"plot": p, "empty": True})
            continue
        c = _BY_KEY.get(r["crop"], {})
        ready = r["ready_at"] <= now_iso()
        secs = 0 if ready else max(0, int((datetime.fromisoformat(r["ready_at"]) - now).total_seconds()))
        pest = (r["pest"] if "pest" in r.keys() else 0) == 1   # 1 = aktivní chrobáci (čeká na záchranu)
        plots.append({"plot": p, "empty": False, "crop": r["crop"], "icon": c.get("icon"),
                      "name": c.get("name"), "reward": c.get("reward"),
                      "ready": ready, "seconds_left": secs,
                      "pest": pest, "rescue_cost": _rescue_cost(c) if pest else 0})
    return {"plots": plots, "crops": _crops_public(_is_sub(user), pest_chance), "n_plots": N_PLOTS,
            "sub": _is_sub(user), "seed_pct": int(SEED_PCT * 100), "seed_pct_sub": int(SEED_PCT_SUB * 100),
            "pest_chance": int(round(pest_chance * 100)),
            "pest_base_chance": int(PEST_CHANCE * 100),
            "pest_min_chance": int(PEST_MIN_CHANCE * 100),
            "rescue_pct": int(PEST_RESCUE_PCT * 100)}


def plant(conn, user, plot: int, crop_key: str) -> dict:
    from .deps import try_debit
    if plot < 0 or plot >= N_PLOTS:
        return {"ok": False, "error": "Neplatný záhon."}
    c = _BY_KEY.get(crop_key)
    if not c:
        return {"ok": False, "error": "Neznámá plodina."}
    if conn.execute("SELECT 1 FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone():
        return {"ok": False, "error": "Záhon je obsazený."}
    seed = _seed_cost(c, _is_sub(user))
    if not try_debit(conn, user["id"], seed, f"Zasazení: {c['name']} 🌱"):
        return {"ok": False, "error": f"Nemáš dost sedláků (semínko {seed})."}
    now = datetime.now(timezone.utc)
    pest = 1 if random.random() < _pest_chance(conn, user["id"]) else 0   # decor reduces pest risk
    conn.execute("INSERT INTO garden (user_id, plot, crop, planted_at, ready_at, pest) VALUES (?,?,?,?,?,?)",
                 (user["id"], plot, crop_key, now.isoformat(), (now + timedelta(hours=c["hours"])).isoformat(), pest))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": c["name"], "cost": seed, "hours": c["hours"]}


def harvest(conn, user, plot: int) -> dict:
    from .deps import add_points
    r = conn.execute("SELECT * FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný záhon."}
    if r["ready_at"] > now_iso():
        return {"ok": False, "error": "Ještě nedorostlo. 🌱"}
    c = _BY_KEY.get(r["crop"], {})
    base = c.get("reward", 0)
    pest = (r["pest"] if "pest" in r.keys() else 0) == 1   # neošetření chrobáci → půlka úrody
    reward = round(base * PEST_PENALTY) if pest else base
    reason = "Sklizeň (chrobáci ji načali) 🐛" if pest else f"Sklizeň: {c.get('name', '?')} 🌾"
    conn.execute("DELETE FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot))
    add_points(conn, user["id"], reward, reason)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "name": c.get("name"), "pest": pest}


def rescue(conn, user, plot: int) -> dict:
    """Zaplať záchranu před chrobáci (% výnosu) → plodina ošetřena, plná sklizeň. Bez záchrany = půlka."""
    from .deps import try_debit
    r = conn.execute("SELECT * FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný záhon."}
    if (r["pest"] if "pest" in r.keys() else 0) != 1:
        return {"ok": False, "error": "Na tomhle záhonu chrobáci nejsou. 🌱"}
    c = _BY_KEY.get(r["crop"], {})
    cost = _rescue_cost(c)
    if not try_debit(conn, user["id"], cost, f"Záchrana před chrobáky: {c.get('name', '?')} 🐛"):
        return {"ok": False, "error": f"Nemáš dost sedláků na postřik ({cost})."}
    conn.execute("UPDATE garden SET pest = 2 WHERE user_id = ? AND plot = ?", (user["id"], plot))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "cost": cost, "balance": bal, "name": c.get("name")}


# ---- Dekorace zahrádky (cosmetic sink: kup → vlastníš → zdobí zahrádku) ----
DECOR = [
    {"key": "sunflower", "icon": "🌻", "name": "Slunečnice", "cost": 500},
    {"key": "tree",      "icon": "🌳", "name": "Strom",      "cost": 1000},
    {"key": "well",      "icon": "⛲", "name": "Studna",     "cost": 2000},
    {"key": "tractor",   "icon": "🚜", "name": "Traktor",    "cost": 3500},
    {"key": "house",     "icon": "🏡", "name": "Chaloupka",  "cost": 5500},
    {"key": "rainbow",   "icon": "🌈", "name": "Duha",       "cost": 9000},
    {"key": "scarecrow", "icon": "🧑‍🌾", "name": "Strašák",   "cost": 15000},
    {"key": "fountain",  "icon": "⛲", "name": "Fontána",    "cost": 30000},
    {"key": "manor",     "icon": "🏰", "name": "Statek",     "cost": 75000},
]
_DECOR_BY_KEY = {d["key"]: d for d in DECOR}


def decor_status(conn, user) -> dict:
    owned = {r["decor_key"] for r in conn.execute(
        "SELECT decor_key FROM garden_decor WHERE user_id = ?", (user["id"],))}
    items = [{"key": d["key"], "icon": d["icon"], "name": d["name"], "cost": d["cost"],
              "owned": d["key"] in owned,
              "pest_reduction": int(round(DECOR_PEST_REDUCTION.get(d["key"], 0.0) * 100))}
             for d in DECOR]
    reduction = sum(DECOR_PEST_REDUCTION.get(k, 0.0) for k in owned)
    return {"items": items, "owned_icons": [d["icon"] for d in DECOR if d["key"] in owned],
            "pest_reduction": int(round(reduction * 100)),
            "pest_chance": int(round(_pest_chance(conn, user["id"]) * 100)),
            "pest_base_chance": int(PEST_CHANCE * 100),
            "pest_min_chance": int(PEST_MIN_CHANCE * 100)}


def buy_decor(conn, user, key: str) -> dict:
    from .deps import try_debit
    d = _DECOR_BY_KEY.get(key)
    if not d:
        return {"ok": False, "error": "Neznámá dekorace."}
    if conn.execute("SELECT 1 FROM garden_decor WHERE user_id = ? AND decor_key = ?",
                    (user["id"], key)).fetchone():
        return {"ok": False, "error": "Tuhle dekoraci už máš. ✅"}
    if not try_debit(conn, user["id"], d["cost"], f"Dekorace zahrádky: {d['name']} {d['icon']}"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({d['cost']})."}
    conn.execute("INSERT INTO garden_decor (user_id, decor_key, created_at) VALUES (?, ?, ?)",
                 (user["id"], key, now_iso()))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": d["name"], "icon": d["icon"]}
