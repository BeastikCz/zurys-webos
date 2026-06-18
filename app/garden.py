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
SEED_PCT = 0.30       # cena semínka = 30 % výnosu (sink; škáluje s plodinou → drahé plodiny gatované kapitálem)
SEED_PCT_SUB = 0.15   # sub perk: poloviční sazba semínka
PEST_CHANCE = 0.30    # šance že plodina chytí škůdce (riziko – zahrádka není 100% jistý plus)
PEST_RESCUE_PCT = 0.25  # záchrana před škůdci = 25 % výnosu (sink). Nezachráníš → sklizeň jen poloviční.
PEST_PENALTY = 0.5    # neošetření škůdci sežerou půlku úrody

# (key, ikona, název, hodiny růstu, výnos při sklizni). Cena semínka = % z výnosu (počítá se dle subu).
CROPS = [
    {"key": "mrkev",    "icon": "🥕", "name": "Mrkev",       "hours": 1,  "reward": 50},
    {"key": "brambory", "icon": "🥔", "name": "Brambory",    "hours": 3,  "reward": 150},
    {"key": "dyne",     "icon": "🎃", "name": "Dýně",        "hours": 12, "reward": 600},
    {"key": "klas",     "icon": "🌾", "name": "Zlatý klas",  "hours": 24, "reward": 1400},
]
_BY_KEY = {c["key"]: c for c in CROPS}


def _is_sub(user) -> bool:
    try:
        return bool(user["is_sub"]) or user["role"] == "admin"
    except (KeyError, IndexError, TypeError):
        return False


def _seed_cost(crop, is_sub: bool) -> int:
    """Cena semínka = % výnosu (sub má poloviční). Min 1."""
    return max(1, round(crop["reward"] * (SEED_PCT_SUB if is_sub else SEED_PCT)))


def _rescue_cost(crop) -> int:
    """Cena záchrany před škůdci = % výnosu. Min 5."""
    return max(5, round(crop["reward"] * PEST_RESCUE_PCT))


def _crops_public(is_sub: bool) -> list:
    out = []
    for c in CROPS:
        seed = _seed_cost(c, is_sub)
        out.append({"key": c["key"], "icon": c["icon"], "name": c["name"], "hours": c["hours"],
                    "reward": c["reward"], "cost": seed, "net": c["reward"] - seed})
    return out


def status(conn, user) -> dict:
    now = datetime.now(timezone.utc)
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
        pest = (r["pest"] if "pest" in r.keys() else 0) == 1   # 1 = aktivní škůdci (čeká na záchranu)
        plots.append({"plot": p, "empty": False, "crop": r["crop"], "icon": c.get("icon"),
                      "name": c.get("name"), "reward": c.get("reward"),
                      "ready": ready, "seconds_left": secs,
                      "pest": pest, "rescue_cost": _rescue_cost(c) if pest else 0})
    return {"plots": plots, "crops": _crops_public(_is_sub(user)), "n_plots": N_PLOTS,
            "sub": _is_sub(user), "seed_pct": int(SEED_PCT * 100), "seed_pct_sub": int(SEED_PCT_SUB * 100)}


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
    pest = 1 if random.random() < PEST_CHANCE else 0   # náhodně škůdci → riziko (zaplať záchranu, nebo půlka sklizně)
    conn.execute("INSERT INTO garden (user_id, plot, crop, planted_at, ready_at, pest) VALUES (?,?,?,?,?,?)",
                 (user["id"], plot, crop_key, now.isoformat(), (now + timedelta(hours=c["hours"])).isoformat(), pest))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal}


def harvest(conn, user, plot: int) -> dict:
    from .deps import add_points
    r = conn.execute("SELECT * FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný záhon."}
    if r["ready_at"] > now_iso():
        return {"ok": False, "error": "Ještě nedorostlo. 🌱"}
    c = _BY_KEY.get(r["crop"], {})
    base = c.get("reward", 0)
    pest = (r["pest"] if "pest" in r.keys() else 0) == 1   # neošetření škůdci → půlka úrody
    reward = round(base * PEST_PENALTY) if pest else base
    reason = "Sklizeň (škůdci ji načali) 🐛" if pest else f"Sklizeň: {c.get('name', '?')} 🌾"
    conn.execute("DELETE FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot))
    add_points(conn, user["id"], reward, reason)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "name": c.get("name"), "pest": pest}


def rescue(conn, user, plot: int) -> dict:
    """Zaplať záchranu před škůdci (% výnosu) → plodina ošetřena, plná sklizeň. Bez záchrany = půlka."""
    from .deps import try_debit
    r = conn.execute("SELECT * FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný záhon."}
    if (r["pest"] if "pest" in r.keys() else 0) != 1:
        return {"ok": False, "error": "Na tomhle záhonu škůdci nejsou. 🌱"}
    c = _BY_KEY.get(r["crop"], {})
    cost = _rescue_cost(c)
    if not try_debit(conn, user["id"], cost, f"Záchrana před škůdci: {c.get('name', '?')} 🐛"):
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
]
_DECOR_BY_KEY = {d["key"]: d for d in DECOR}


def decor_status(conn, user) -> dict:
    owned = {r["decor_key"] for r in conn.execute(
        "SELECT decor_key FROM garden_decor WHERE user_id = ?", (user["id"],))}
    items = [{"key": d["key"], "icon": d["icon"], "name": d["name"], "cost": d["cost"],
              "owned": d["key"] in owned} for d in DECOR]
    return {"items": items, "owned_icons": [d["icon"] for d in DECOR if d["key"] in owned]}


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
