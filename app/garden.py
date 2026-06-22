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
PEST_CHANCE = 0.85    # base pest chance; full current decor lowers 85 % to 25 %
PEST_RESCUE_PCT = 0.20  # záchrana před chrobáci = 20 % výnosu (sink). Nezachráníš → sklizeň jen poloviční.
PEST_PENALTY = 0.5    # neošetření chrobáci sežerou půlku úrody
PEST_MIN_CHANCE = 0.10   # dolní strop šance i s plnou výbavou dekorací
PEST_SPAWN_MIN = 0.15    # chrobáci se objeví DYNAMICKY nejdřív po 15 % doby růstu (ne při zasazení)
PEST_SPAWN_MAX = 0.85    # … nejpozdějc po 85 %
PEST_WINDOW_FRAC = 0.30  # okno na záchranu = 30 % doby růstu; po něm sežerou půlku úrody (zamčeno)
GOLDEN_CHANCE = 0.03     # šance na ZLATOU (vzácnou) sklizeň ze zdravé úrody → ×3 výnos i XP
GOLDEN_MULT = 3

# (key, ikona, název, hodiny růstu, výnos při sklizni). Cena semínka = % z výnosu (počítá se dle subu).
CROPS = [
    {"key": "mrkev",    "icon": "🥕", "name": "Mrkev",       "hours": 1,  "reward": 50},
    {"key": "brambory", "icon": "🥔", "name": "Brambory",    "hours": 3,  "reward": 150},
    {"key": "dyne",     "icon": "🎃", "name": "Dýně",        "hours": 12, "reward": 600},
    {"key": "klas",     "icon": "🌾", "name": "Zlatý klas",  "hours": 24, "reward": 1400},
]
_BY_KEY = {c["key"]: c for c in CROPS}

DECOR_PEST_REDUCTION = {   # součet = 60 pb → plná výbava sráží 85 % na 25 %
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


PLOT_DECORS = ("manor", "stodola", "hrad", "palac")   # expanze farmy: každá budova = +1 záhon (base 4 → max 8)


def _n_plots(conn, user_id: int) -> int:
    """Počet záhonů = base + 1 za KAŽDOU vlastněnou expanzní budovu (Statek/Stodola/Hrad/Palác)."""
    owned = conn.execute(
        "SELECT COUNT(*) c FROM garden_decor WHERE user_id = ? AND decor_key IN (?,?,?,?)",
        (user_id, *PLOT_DECORS)).fetchone()["c"]
    return N_PLOTS + owned


def _pest_spawn_at(c: dict, now: datetime) -> str:
    """Náhodný čas (ISO) kdy se chrobáci objeví během růstu plodiny c (mezi 15–85 % doby)."""
    frac = PEST_SPAWN_MIN + random.random() * (PEST_SPAWN_MAX - PEST_SPAWN_MIN)
    return (now + timedelta(hours=c.get("hours", 1) * frac)).isoformat()


def _pest_state(r, now: datetime):
    """Stav chrobáků a sekundy do další změny stavu.

    incoming = do příchodu · active = do konce záchrany · none/eaten = 0.
    """
    pa = (r["pest_at"] if "pest_at" in r.keys() else None)
    rescued = (r["pest"] if "pest" in r.keys() else 0) == 2
    if not pa or rescued:
        return ("none", 0)
    pa_dt = datetime.fromisoformat(pa)
    c = _BY_KEY.get(r["crop"], {})
    deadline = pa_dt + timedelta(hours=c.get("hours", 1) * PEST_WINDOW_FRAC)
    if now < pa_dt:
        return ("incoming", max(1, int((pa_dt - now).total_seconds())))
    if now < deadline:
        return ("active", max(0, int((deadline - now).total_seconds())))
    return ("eaten", 0)


def _crops_public(is_sub: bool, pest_chance: float) -> list:
    # XP ze sklizně = výnos × GARDEN_XP_FACTOR × (SUB bonus GARDEN_XP_SUB_MULT). UNCAPPED (mimo denní strop).
    # NON-SUB jen faktor 0.2 (klas 280); SUB má vlastní bonus (klas ≈ 900). MUSÍ sedět s deps._xp_award ('garden').
    # Plný výnos – pest úrodu/XP půlí, karta ukazuje plný potenciál (bez chrobáků).
    from .deps import GARDEN_XP_SUB_MULT, GARDEN_XP_FACTOR
    xp_mult = GARDEN_XP_FACTOR * (GARDEN_XP_SUB_MULT if is_sub else 1.0)
    out = []
    for c in CROPS:
        seed = _seed_cost(c, is_sub)
        expected_no_rescue = round(c["reward"] * ((1 - pest_chance) + pest_chance * PEST_PENALTY) - seed)
        expected_rescue = round(c["reward"] - seed - (_rescue_cost(c) * pest_chance))
        out.append({"key": c["key"], "icon": c["icon"], "name": c["name"], "hours": c["hours"],
                    "reward": c["reward"], "cost": seed, "net": c["reward"] - seed,
                    "xp": round(c["reward"] * xp_mult),
                    "expected_no_rescue": expected_no_rescue,
                    "expected_rescue": expected_rescue})
    return out


def status(conn, user) -> dict:
    now = datetime.now(timezone.utc)
    pest_chance = _pest_chance(conn, user["id"])
    rows = {r["plot"]: r for r in conn.execute(
        "SELECT * FROM garden WHERE user_id = ?", (user["id"],))}
    plots = []
    n_plots = _n_plots(conn, user["id"])              # base + Statek (manor) bonus záhon
    for p in range(n_plots):
        r = rows.get(p)
        if not r:
            plots.append({"plot": p, "empty": True})
            continue
        c = _BY_KEY.get(r["crop"], {})
        ready = r["ready_at"] <= now_iso()
        secs = 0 if ready else max(0, int((datetime.fromisoformat(r["ready_at"]) - now).total_seconds()))
        pstate, transition_left = _pest_state(r, now)
        plots.append({"plot": p, "empty": False, "crop": r["crop"], "icon": c.get("icon"),
                      "name": c.get("name"), "reward": c.get("reward"),
                      "ready": ready, "seconds_left": secs,
                      "pest": pstate == "active", "eaten": pstate == "eaten",
                      "pest_in": transition_left if pstate == "incoming" else 0,
                      "rescue_left": transition_left if pstate == "active" else 0,
                      "rescue_cost": _rescue_cost(c) if pstate == "active" else 0})
    return {"plots": plots, "crops": _crops_public(_is_sub(user), pest_chance), "n_plots": n_plots,
            "sub": _is_sub(user), "seed_pct": int(SEED_PCT * 100), "seed_pct_sub": int(SEED_PCT_SUB * 100),
            "pest_chance": int(round(pest_chance * 100)),
            "pest_base_chance": int(PEST_CHANCE * 100),
            "pest_min_chance": int(PEST_MIN_CHANCE * 100),
            "rescue_pct": int(PEST_RESCUE_PCT * 100)}


def plant(conn, user, plot: int, crop_key: str) -> dict:
    from .deps import try_debit
    if plot < 0 or plot >= _n_plots(conn, user["id"]):
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
    pest_at = _pest_spawn_at(c, now) if random.random() < _pest_chance(conn, user["id"]) else None   # decor snižuje šanci
    conn.execute("INSERT INTO garden (user_id, plot, crop, planted_at, ready_at, pest, pest_at) VALUES (?,?,?,?,?,0,?)",
                 (user["id"], plot, crop_key, now.isoformat(), (now + timedelta(hours=c["hours"])).isoformat(), pest_at))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": c["name"], "cost": seed, "hours": c["hours"]}


def _harvest_reward(c: dict, pest: bool):
    """(reward, reason, golden). Zdravá úroda má šanci na ZLATOU sklizeň (×GOLDEN_MULT – víc výnosu i XP).
    Chrobáci = půlka úrody, bez zlaté."""
    base = c.get("reward", 0)
    if pest:
        return round(base * PEST_PENALTY), "Sklizeň (chrobáci ji načali) 🐛", False
    if random.random() < GOLDEN_CHANCE:
        return base * GOLDEN_MULT, f"Zlatá sklizeň: {c.get('name', '?')} 🌟 (×{GOLDEN_MULT})", True
    return base, f"Sklizeň: {c.get('name', '?')} 🌾", False


def _award_harvest(conn, user_id, reward, reason, golden):
    """Připíše sklizeň. ZLATÁ = ×GOLDEN_MULT SEDLÁCI ale XP jen z base (neměň XP na produ):
    base s XP + zlatý bonus BEZ XP. Necommituje (commituje caller)."""
    from .deps import add_points
    if golden and GOLDEN_MULT > 1:
        base = reward // GOLDEN_MULT
        add_points(conn, user_id, base, reason)                               # base sedláci + XP (1×)
        add_points(conn, user_id, reward - base, "Zlatý bonus 🌟", xp=False)  # extra sedláci BEZ XP
    else:
        add_points(conn, user_id, reward, reason)


def harvest(conn, user, plot: int) -> dict:
    from .deps import add_points
    r = conn.execute("SELECT * FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný záhon."}
    if r["ready_at"] > now_iso():
        return {"ok": False, "error": "Ještě nedorostlo. 🌱"}
    c = _BY_KEY.get(r["crop"], {})
    pstate, _left = _pest_state(r, datetime.now(timezone.utc))
    damaged = pstate in ("active", "eaten")   # chrobáci se objevili a nezachránil → půlka úrody
    reward, reason, golden = _harvest_reward(c, damaged)
    # ATOMICKY: odměnu připíše JEN ten request, který reálně smazal řádek (rowcount==1).
    # Bez toho dva souběžné /garden/harvest na stejný záhon obě připíšou odměnu (double-pay).
    if conn.execute("DELETE FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Tenhle záhon je už sklizený. 🌾"}
    _award_harvest(conn, user["id"], reward, reason, golden)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "name": c.get("name"), "pest": damaged, "golden": golden}


def harvest_all(conn, user) -> dict:
    """Sklidí VŠECHNY dozrálé záhony naráz (atomicky per řádek). Vrací počet, výnos a kolik bylo zlatých."""
    from .deps import add_points
    ready = conn.execute("SELECT plot, crop, pest, pest_at FROM garden WHERE user_id = ? AND ready_at <= ?",
                         (user["id"], now_iso())).fetchall()
    total = count = golds = 0
    now_dt = datetime.now(timezone.utc)
    for r in ready:
        c = _BY_KEY.get(r["crop"], {})
        pstate, _left = _pest_state(r, now_dt)
        reward, reason, golden = _harvest_reward(c, pstate in ("active", "eaten"))
        if conn.execute("DELETE FROM garden WHERE user_id = ? AND plot = ?", (user["id"], r["plot"])).rowcount != 1:
            continue   # někdo to mezitím sklidil (souběh)
        _award_harvest(conn, user["id"], reward, reason, golden)
        total += reward; count += 1; golds += 1 if golden else 0
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "count": count, "total": total, "golden": golds, "balance": bal}


def plant_all(conn, user, crop_key: str) -> dict:
    """Zasadí plodinu na VŠECHNY prázdné záhony (dokud stačí sedláci). Vrací počet zasazených + cenu."""
    from .deps import try_debit, add_points
    c = _BY_KEY.get(crop_key)
    if not c:
        return {"ok": False, "error": "Neznámá plodina."}
    n = _n_plots(conn, user["id"])
    occupied = {r["plot"] for r in conn.execute("SELECT plot FROM garden WHERE user_id = ?", (user["id"],))}
    empty = [p for p in range(n) if p not in occupied]
    if not empty:
        return {"ok": False, "error": "Všechny záhony jsou obsazené. 🌱"}
    seed = _seed_cost(c, _is_sub(user))
    pest_chance = _pest_chance(conn, user["id"])      # hoist – v rámci requestu se nemění
    now = datetime.now(timezone.utc)
    ready = (now + timedelta(hours=c["hours"])).isoformat()
    planted = spent = 0
    for p in empty:
        if not try_debit(conn, user["id"], seed, f"Zasazení: {c['name']} 🌱"):
            break   # došly sedláci → zasadíme kolik šlo
        pest_at = _pest_spawn_at(c, now) if random.random() < pest_chance else None
        try:
            conn.execute("INSERT INTO garden (user_id, plot, crop, planted_at, ready_at, pest, pest_at) VALUES (?,?,?,?,?,0,?)",
                         (user["id"], p, crop_key, now.isoformat(), ready, pest_at))
        except sqlite3.IntegrityError:                # záhon mezitím obsazen (souběh) → vrať semínko, přeskoč
            add_points(conn, user["id"], seed, "Vrácení semínka (záhon obsazen)", xp=False)
            continue
        planted += 1; spent += seed
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    if planted == 0:
        return {"ok": False, "error": f"Nemáš dost sedláků (semínko {seed})."}
    return {"ok": True, "planted": planted, "spent": spent, "name": c["name"], "hours": c["hours"], "balance": bal}


def rescue(conn, user, plot: int) -> dict:
    """Zaplať záchranu když jsou AKTIVNÍ chrobáci (v okně). Po okně už ji sežrali (zamčeno → půlka)."""
    from .deps import try_debit
    r = conn.execute("SELECT * FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný záhon."}
    pstate, _left = _pest_state(r, datetime.now(timezone.utc))
    if pstate == "eaten":
        return {"ok": False, "error": "Pozdě! Chrobáci už úrodu načali — sklidíš jen půlku. 🐛💀"}
    if pstate != "active":
        return {"ok": False, "error": "Na tomhle záhonu chrobáci nejsou. 🌱"}
    c = _BY_KEY.get(r["crop"], {})
    cost = _rescue_cost(c)
    # ATOMICKY: označ za zachráněný (pest 0→2) – cenu strhne jen request, který reálně přepnul (rowcount==1).
    if conn.execute("UPDATE garden SET pest = 2 WHERE user_id = ? AND plot = ? AND pest = 0 AND pest_at IS NOT NULL",
                    (user["id"], plot)).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Na tomhle záhonu chrobáci nejsou. 🌱"}
    if not try_debit(conn, user["id"], cost, f"Záchrana před chrobáky: {c.get('name', '?')} 🐛"):
        conn.execute("UPDATE garden SET pest = 0 WHERE user_id = ? AND plot = ?", (user["id"], plot))  # vrať
        conn.commit()
        return {"ok": False, "error": f"Nemáš dost sedláků na postřik ({cost})."}
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
    {"key": "manor",     "icon": "🏘️", "name": "Statek",     "cost": 75000},
    {"key": "stodola",   "icon": "🛖", "name": "Stodola",    "cost": 250000},
    {"key": "hrad",      "icon": "🏰", "name": "Hrad",       "cost": 500000},
    {"key": "palac",     "icon": "🏯", "name": "Palác",      "cost": 1000000},
]
_DECOR_BY_KEY = {d["key"]: d for d in DECOR}


def decor_status(conn, user) -> dict:
    owned = {r["decor_key"] for r in conn.execute(
        "SELECT decor_key FROM garden_decor WHERE user_id = ?", (user["id"],))}
    items = [{"key": d["key"], "icon": d["icon"], "name": d["name"], "cost": d["cost"],
              "owned": d["key"] in owned,
              "pest_reduction": int(round(DECOR_PEST_REDUCTION.get(d["key"], 0.0) * 100)),
              "plots": 1 if d["key"] in PLOT_DECORS else 0}      # expanzní budova = +1 záhon
             for d in DECOR]
    reduction = sum(DECOR_PEST_REDUCTION.get(k, 0.0) for k in owned)
    return {"items": items, "owned_icons": [d["icon"] for d in DECOR if d["key"] in owned],
            "pest_reduction": int(round(reduction * 100)),
            "pest_chance": int(round(_pest_chance(conn, user["id"]) * 100)),
            "pest_base_chance": int(PEST_CHANCE * 100),
            "pest_min_chance": int(PEST_MIN_CHANCE * 100)}


def buy_decor(conn, user, key: str) -> dict:
    import sqlite3 as _sqlite3
    from .deps import try_debit
    d = _DECOR_BY_KEY.get(key)
    if not d:
        return {"ok": False, "error": "Neznámá dekorace."}
    if conn.execute("SELECT 1 FROM garden_decor WHERE user_id = ? AND decor_key = ?",
                    (user["id"], key)).fetchone():
        return {"ok": False, "error": "Tuhle dekoraci už máš. ✅"}
    if not try_debit(conn, user["id"], d["cost"], f"Dekorace zahrádky: {d['name']} {d['icon']}"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({d['cost']})."}
    try:
        conn.execute("INSERT INTO garden_decor (user_id, decor_key, created_at) VALUES (?, ?, ?)",
                     (user["id"], key, now_iso()))
        conn.commit()
    except _sqlite3.IntegrityError:
        # Concurrent request already bought same decor — refund and signal duplicate.
        from .deps import add_points
        conn.rollback()
        add_points(conn, user["id"], d["cost"], f"Vrácení za dekoraci (souběh): {d['name']}", xp=False)
        conn.commit()
        return {"ok": False, "error": "Tuhle dekoraci už máš. ✅"}
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": d["name"], "icon": d["icon"]}


# ---- Žebříček zahradníků (top dle celkem sklizených sedláků) ----
def leaderboard(conn, limit: int = 10) -> list:
    """Top zahradníci dle celkem SKLIZENÝCH sedláků (gross z points_log 'Sklizeň…/Zlatá sklizeň…')."""
    rows = conn.execute(
        "SELECT pl.user_id, u.username, u.avatar_url, SUM(pl.change) AS total "
        "FROM points_log pl JOIN users u ON u.id = pl.user_id "
        "WHERE pl.change > 0 AND LOWER(pl.reason) LIKE '%skliz%' "
        "GROUP BY pl.user_id ORDER BY total DESC, u.username ASC LIMIT ?", (limit,)).fetchall()
    return [{"rank": i + 1, "username": r["username"], "avatar_url": r["avatar_url"], "total": r["total"]}
            for i, r in enumerate(rows)]
