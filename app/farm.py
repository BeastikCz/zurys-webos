"""Statek (mini-farma): kup zvíře → nakrm → produkuje v čase → seber produkt (XP + sedláci).

Mini-hra nad zahrádkou. Hloubka:
 • 6 zvířat (slepička/koza/ovce/kráva + sub-only jednorožec 🦄 + utility kůň 🐴 = pasivní +% produkce)
 • LEVELY zvířat: každé krmení += fed_count → level (1..MAX) → větší výnos + rychlejší cyklus
 • KRMIVO: zásoba padá ze sklizně zahrádky (propojení zahrada↔statek) → krmí zdarma místo sedláků
 • PRODEJ zvířete (část ceny zpět, uvolní slot) → flexibilita / dokončení sbírky
 • SBÍRKA: vlastni všechny druhy → jednorázový bonus (odznak ekonomicky safe)
 • Golden produkt 3 % → ×3 sedláci BEZ XP (jako zahrádka). Hlad = nenakrmené neprodukuje (aktivní péče).
XP přes reason 'Statek: …' → classify_xp 'garden' bucket (uncapped, sub bonus). ŽÁDNÁ změna XP modelu.
"""
import random
import sqlite3
from datetime import datetime, timezone, timedelta

from .db import now_iso

BASE_SLOTS = 2
SUB_SLOTS = 1
GOLDEN_CHANCE = 0.03
GOLDEN_MULT = 3
SELL_REFUND_PCT = 0.5          # prodej zvířete = % ceny zpět
FEED_PER_LEVEL = 5             # kolik nakrmení = +1 level
MAX_LEVEL = 10
LEVEL_REWARD_STEP = 0.10       # +10 % výnosu za level nad 1
LEVEL_SPEED_STEP = 0.03        # −3 % času cyklu za level (floor 40 %)
HARVEST_KRMIVO = 1             # kolik krmiva padne za 1 sklizeň zahrádky
COLLECTION_REWARD = 25000      # jednorázový bonus za kompletní sbírku

# (key, ikona, název, cena; produkční: feed/hours/reward/product/pico; speciální: sub_only / utility+prod_bonus)
ANIMALS = [
    {"key": "chicken", "icon": "🐔", "name": "Slepice",  "cost": 2000,  "feed": 80,  "hours": 2,  "reward": 130,  "product": "vejce", "pico": "🥚"},
    {"key": "goat",    "icon": "🐐", "name": "Koza",     "cost": 6000,  "feed": 150, "hours": 4,  "reward": 300,  "product": "mléko", "pico": "🥛"},
    {"key": "sheep",   "icon": "🐑", "name": "Ovce",     "cost": 12000, "feed": 250, "hours": 6,  "reward": 550,  "product": "vlnu",  "pico": "🧶"},
    {"key": "cow",     "icon": "🐄", "name": "Kráva",    "cost": 40000, "feed": 500, "hours": 12, "reward": 1400, "product": "sýr",   "pico": "🧀"},
    # klíč "unicorn" zůstává (DB), zobrazení = Kůň (sub-only producent)
    {"key": "unicorn", "icon": "🐴", "name": "Kůň",      "cost": 80000, "feed": 400, "hours": 8,  "reward": 1100, "product": "hnůj",  "pico": "💩", "sub_only": True},
    # klíč "horse" zůstává (DB), zobrazení = Pes (utility – hlídá statek, pasivní +%)
    {"key": "horse",   "icon": "🐕", "name": "Pes",      "cost": 150000, "utility": True, "prod_bonus": 0.10},
]
_BY_KEY = {a["key"]: a for a in ANIMALS}


def _is_sub(user) -> bool:
    try:
        return bool(user["is_sub"]) or user["role"] == "admin"
    except (KeyError, IndexError, TypeError):
        return False


def _n_slots(conn, user) -> int:
    return BASE_SLOTS + (SUB_SLOTS if _is_sub(user) else 0)


def _level(fed_count: int) -> int:
    return max(1, min(MAX_LEVEL, 1 + (fed_count or 0) // FEED_PER_LEVEL))


def _reward_at(a: dict, level: int, bonus: float) -> int:
    """Výnos zvířete s levelem (+10 %/lvl) a pasivním bonusem (kůň)."""
    base = a.get("reward", 0)
    return int(round(base * (1 + LEVEL_REWARD_STEP * (level - 1)) * (1 + bonus)))


def _hours_at(a: dict, level: int) -> float:
    """Délka cyklu s levelem (−3 %/lvl, floor 40 %)."""
    return a.get("hours", 1) * max(0.4, 1 - LEVEL_SPEED_STEP * (level - 1))


def _prod_bonus(conn, uid: int) -> float:
    """Součet pasivních bonusů z vlastněných utility zvířat (kůň +10 %)."""
    keys = [r["animal_key"] for r in conn.execute("SELECT animal_key FROM farm_animals WHERE user_id = ?", (uid,))]
    return sum(_BY_KEY.get(k, {}).get("prod_bonus", 0.0) for k in keys if _BY_KEY.get(k, {}).get("utility"))


def _feed_stock(conn, uid: int) -> int:
    r = conn.execute("SELECT feed_stock FROM users WHERE id = ?", (uid,)).fetchone()
    return (r["feed_stock"] if r else 0) or 0


def add_krmivo(conn, uid: int, n: int = HARVEST_KRMIVO) -> None:
    """Přidá krmivo (volá zahrádka při sklizni). Necommituje – commit dělá caller."""
    conn.execute("UPDATE users SET feed_stock = COALESCE(feed_stock, 0) + ? WHERE id = ?", (n, uid))


def _animals_public(conn, user) -> list:
    owned = {r["animal_key"] for r in conn.execute(
        "SELECT animal_key FROM farm_animals WHERE user_id = ?", (user["id"],))}
    sub = _is_sub(user)
    out = []
    for a in ANIMALS:
        out.append({"key": a["key"], "icon": a["icon"], "name": a["name"], "cost": a["cost"],
                    "utility": a.get("utility", False), "prod_bonus": int(a.get("prod_bonus", 0) * 100),
                    "sub_only": a.get("sub_only", False), "locked": a.get("sub_only", False) and not sub,
                    "owned": a["key"] in owned,
                    "feed": a.get("feed", 0), "hours": a.get("hours", 0),
                    "reward": a.get("reward", 0), "product": a.get("product", ""), "pico": a.get("pico", "")})
    return out


def status(conn, user) -> dict:
    now = datetime.now(timezone.utc)
    n_slots = _n_slots(conn, user)
    bonus = _prod_bonus(conn, user["id"])
    rows = {r["slot"]: r for r in conn.execute("SELECT * FROM farm_animals WHERE user_id = ?", (user["id"],))}
    slots = []
    for s in range(n_slots):
        r = rows.get(s)
        if not r:
            slots.append({"slot": s, "empty": True})
            continue
        a = _BY_KEY.get(r["animal_key"], {})
        lvl = _level(r["fed_count"])
        if a.get("utility"):
            slots.append({"slot": s, "empty": False, "animal": r["animal_key"], "icon": a.get("icon"),
                          "name": a.get("name"), "utility": True,
                          "bonus": int(a.get("prod_bonus", 0) * 100), "level": lvl, "max_level": MAX_LEVEL})
            continue
        ready_at = r["ready_at"] or ""
        if not ready_at:
            state, secs = "hungry", 0
        elif ready_at <= now_iso():
            state, secs = "ready", 0
        else:
            state = "growing"
            secs = max(0, int((datetime.fromisoformat(ready_at) - now).total_seconds()))
        slots.append({"slot": s, "empty": False, "animal": r["animal_key"], "icon": a.get("icon"),
                      "name": a.get("name"), "product": a.get("product"), "pico": a.get("pico"),
                      "feed": a.get("feed"), "reward": _reward_at(a, lvl, bonus),
                      "state": state, "seconds_left": secs, "level": lvl, "max_level": MAX_LEVEL,
                      "fed_count": r["fed_count"] or 0, "feed_per_level": FEED_PER_LEVEL,
                      "utility": False})
    have_coll = {r["animal_key"] for r in conn.execute(
        "SELECT animal_key FROM farm_collection WHERE user_id = ?", (user["id"],))}
    all_keys = {a["key"] for a in ANIMALS}
    return {"slots": slots, "animals": _animals_public(conn, user), "n_slots": n_slots, "sub": _is_sub(user),
            "golden_pct": int(GOLDEN_CHANCE * 100), "golden_mult": GOLDEN_MULT, "krmivo": _feed_stock(conn, user["id"]),
            "prod_bonus": int(bonus * 100),
            "collection": {"have": len(all_keys & have_coll), "total": len(all_keys),
                           "complete": all_keys.issubset(have_coll), "reward": COLLECTION_REWARD}}


def _note_collection(conn, uid: int, key: str):
    """Zapíše druh do sbírky. Když kompletní (všechny druhy) → 1× bonus + notif."""
    conn.execute("INSERT OR IGNORE INTO farm_collection (user_id, animal_key, created_at) VALUES (?,?,?)",
                 (uid, key, now_iso()))
    have = {r["animal_key"] for r in conn.execute("SELECT animal_key FROM farm_collection WHERE user_id = ?", (uid,))}
    all_keys = {a["key"] for a in ANIMALS}
    if all_keys.issubset(have) and "__complete__" not in have:
        from .deps import add_points, notify
        conn.execute("INSERT OR IGNORE INTO farm_collection (user_id, animal_key, created_at) VALUES (?,?,?)",
                     (uid, "__complete__", now_iso()))
        add_points(conn, uid, COLLECTION_REWARD, "Statek: kompletní sbírka 🏆", xp=False)
        notify(conn, uid, "🏆", "Kompletní sbírka statku!",
               f"Máš všechna zvířata na statku – bonus +{COLLECTION_REWARD} sedláků! 🚜", "#/statek")


def buy(conn, user, animal_key: str) -> dict:
    from .deps import try_debit
    a = _BY_KEY.get(animal_key)
    if not a:
        return {"ok": False, "error": "Neznámé zvíře."}
    if a.get("sub_only") and not _is_sub(user):
        return {"ok": False, "error": f"{a['name']} {a['icon']} je jen pro suby. 💜"}
    n = _n_slots(conn, user)
    occupied = {r["slot"] for r in conn.execute("SELECT slot FROM farm_animals WHERE user_id = ?", (user["id"],))}
    empty = [s for s in range(n) if s not in occupied]
    if not empty:
        return {"ok": False, "error": "Plný statek – prodej zvíře nebo si jako sub odemkni víc slotů. 🚜"}
    slot = empty[0]
    if not try_debit(conn, user["id"], a["cost"], f"Statek: koupě {a['name']} {a['icon']}"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({a['cost']})."}
    try:
        conn.execute("INSERT INTO farm_animals (user_id, slot, animal_key, ready_at, fed_count, bought_at) "
                     "VALUES (?, ?, ?, '', 0, ?)", (user["id"], slot, animal_key, now_iso()))
    except sqlite3.IntegrityError:
        from .deps import add_points
        conn.rollback()
        add_points(conn, user["id"], a["cost"], "Vrácení za zvíře (souběh)", xp=False)
        conn.commit()
        return {"ok": False, "error": "Slot se mezitím obsadil. Zkus znovu."}
    _note_collection(conn, user["id"], animal_key)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": a["name"], "icon": a["icon"], "slot": slot,
            "utility": a.get("utility", False)}


def sell(conn, user, slot: int) -> dict:
    """Prodá zvíře ze slotu (vrátí část ceny, uvolní slot). Sbírku to NEodebere (druh zůstává nasbíraný)."""
    from .deps import add_points
    r = conn.execute("SELECT * FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný slot."}
    a = _BY_KEY.get(r["animal_key"], {})
    refund = int(round(a.get("cost", 0) * SELL_REFUND_PCT))
    if conn.execute("DELETE FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Už prodáno."}
    if refund > 0:
        add_points(conn, user["id"], refund, f"Statek: prodej {a.get('name', '?')} 💰", xp=False)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "refund": refund, "name": a.get("name")}


def feed(conn, user, slot: int) -> dict:
    """Nakrm hladové zvíře → spustí cyklus. Použije KRMIVO (zdarma) pokud je, jinak sedláky."""
    from .deps import try_debit
    r = conn.execute("SELECT * FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný slot."}
    a = _BY_KEY.get(r["animal_key"], {})
    if a.get("utility"):
        return {"ok": False, "error": f"{a.get('name', '?')} nepotřebuje krmit – dává pasivní bonus. 🐴"}
    if r["ready_at"]:
        return {"ok": False, "error": "Zvíře už produkuje (nebo má hotovo). 🐔"}
    used_krmivo = False
    if _feed_stock(conn, user["id"]) >= 1:                       # krmivo má přednost (zdarma, ze zahrádky)
        conn.execute("UPDATE users SET feed_stock = feed_stock - 1 WHERE id = ?", (user["id"],))
        used_krmivo = True
    elif not try_debit(conn, user["id"], a.get("feed", 0), f"Statek: krmivo {a.get('name', '?')} 🌾"):
        return {"ok": False, "error": f"Nemáš krmivo ani dost sedláků na krmení ({a.get('feed', 0)})."}
    fed_count = (r["fed_count"] or 0) + 1
    lvl_before, lvl_after = _level(r["fed_count"]), _level(fed_count)
    ready = (datetime.now(timezone.utc) + timedelta(hours=_hours_at(a, lvl_after))).isoformat()
    conn.execute("UPDATE farm_animals SET ready_at = ?, fed_count = ? WHERE user_id = ? AND slot = ?",
                 (ready, fed_count, user["id"], slot))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": a.get("name"), "used_krmivo": used_krmivo,
            "leveled_up": lvl_after > lvl_before, "level": lvl_after}


def _award_product(conn, user_id, a, level, bonus):
    from .deps import add_points
    base = _reward_at(a, level, bonus)
    golden = random.random() < GOLDEN_CHANCE
    add_points(conn, user_id, base, f"Statek: {a['product']} {a['pico']}")
    if golden:
        add_points(conn, user_id, base * (GOLDEN_MULT - 1), f"Statek: zlaté {a['product']} 🌟", xp=False)
    return base * (GOLDEN_MULT if golden else 1), golden


def collect(conn, user, slot: int) -> dict:
    r = conn.execute("SELECT * FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný slot."}
    a = _BY_KEY.get(r["animal_key"], {})
    if a.get("utility"):
        return {"ok": False, "error": "Tady není co sbírat. 🐴"}
    if not r["ready_at"]:
        return {"ok": False, "error": "Zvíře má hlad – nejdřív nakrm. 🌾"}
    if r["ready_at"] > now_iso():
        return {"ok": False, "error": "Ještě se nevyprodukovalo. ⏳"}
    if conn.execute("UPDATE farm_animals SET ready_at = '' WHERE user_id = ? AND slot = ? AND ready_at = ?",
                    (user["id"], slot, r["ready_at"])).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Už sebráno. 🥚"}
    reward, golden = _award_product(conn, user["id"], a, _level(r["fed_count"]), _prod_bonus(conn, user["id"]))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "golden": golden, "balance": bal,
            "name": a.get("name"), "product": a.get("product"), "pico": a.get("pico")}


def collect_all(conn, user) -> dict:
    bonus = _prod_bonus(conn, user["id"])
    ready = conn.execute("SELECT slot, animal_key, ready_at, fed_count FROM farm_animals "
                         "WHERE user_id = ? AND ready_at != '' AND ready_at <= ?",
                         (user["id"], now_iso())).fetchall()
    total = count = golds = 0
    for r in ready:
        a = _BY_KEY.get(r["animal_key"], {})
        if a.get("utility"):
            continue
        if conn.execute("UPDATE farm_animals SET ready_at = '' WHERE user_id = ? AND slot = ? AND ready_at = ?",
                        (user["id"], r["slot"], r["ready_at"])).rowcount != 1:
            continue
        reward, golden = _award_product(conn, user["id"], a, _level(r["fed_count"]), bonus)
        total += reward; count += 1; golds += 1 if golden else 0
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "count": count, "total": total, "golden": golds, "balance": bal}
