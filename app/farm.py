"""Statek (mini-farma): kup zvíře → nakrm (sedláci) → produkuje v čase → seber produkt (XP + sedláci).

Rozšíření zahrádky. Loop: koupě (sink, 1×) → krmení (sink, opakované) → cyklus → produkt = odměna.
Zvíře PERSISTUJE (nekupuješ znovu, jen krmíš). Hlad = nenakrmené nic neprodukuje (aktivní péče,
nutí se vrátit – jako chrobáci v zahrádce). Golden produkt = ×3 SEDLÁCI ale BEZ XP (jako zahrádka).
Sloty = base + sub bonus. XP se uděluje přes reason ('Statek: …' → classify_xp 'garden' bucket,
uncapped, sub bonus) – žádná změna XP modelu, jen nový zdroj forward XP.

P1 (MVP): jen 🐔 slepička. Struktura připravená pro koza/ovce/kráva/kůň (P2).
"""
import random
import sqlite3
from datetime import datetime, timezone, timedelta

from .db import now_iso

BASE_SLOTS = 2          # kolik zvířat zdarma (base)
SUB_SLOTS = 1           # sub má +1 slot navíc
GOLDEN_CHANCE = 0.03    # šance na ZLATÝ produkt → ×3 sedláci (BEZ XP, jako zahrádka)
GOLDEN_MULT = 3

# (key, ikona, název, cena koupě, krmivo sedláků/cyklus, hodiny cyklu, výnos sedláků, produkt, ikona produktu)
ANIMALS = [
    {"key": "chicken", "icon": "🐔", "name": "Slepička", "cost": 2000,
     "feed": 80, "hours": 2, "reward": 130, "product": "vejce", "pico": "🥚"},
    # P2: koza (mléko), ovce (vlna), kráva (sýr), kůň (utility)
]
_BY_KEY = {a["key"]: a for a in ANIMALS}


def _is_sub(user) -> bool:
    try:
        return bool(user["is_sub"]) or user["role"] == "admin"
    except (KeyError, IndexError, TypeError):
        return False


def _n_slots(conn, user) -> int:
    """Počet slotů na zvířata = base + sub bonus (P2: + budova Chlév)."""
    return BASE_SLOTS + (SUB_SLOTS if _is_sub(user) else 0)


def _animals_public() -> list:
    out = []
    for a in ANIMALS:
        out.append({"key": a["key"], "icon": a["icon"], "name": a["name"], "cost": a["cost"],
                    "feed": a["feed"], "hours": a["hours"], "reward": a["reward"],
                    "product": a["product"], "pico": a["pico"],
                    "net": a["reward"] - a["feed"]})
    return out


def status(conn, user) -> dict:
    """Stav statku: sloty (prázdný / zvíře hladové / roste / hotovo) + katalog zvířat."""
    now = datetime.now(timezone.utc)
    n_slots = _n_slots(conn, user)
    rows = {r["slot"]: r for r in conn.execute(
        "SELECT * FROM farm_animals WHERE user_id = ?", (user["id"],))}
    slots = []
    for s in range(n_slots):
        r = rows.get(s)
        if not r:
            slots.append({"slot": s, "empty": True})
            continue
        a = _BY_KEY.get(r["animal_key"], {})
        ready_at = r["ready_at"] or ""
        if not ready_at:
            state, secs = "hungry", 0
        elif ready_at <= now_iso():
            state, secs = "ready", 0
        else:
            state = "growing"
            secs = max(0, int((datetime.fromisoformat(ready_at) - now).total_seconds()))
        slots.append({"slot": s, "empty": False, "animal": r["animal_key"],
                      "icon": a.get("icon"), "name": a.get("name"), "product": a.get("product"),
                      "pico": a.get("pico"), "reward": a.get("reward"), "feed": a.get("feed"),
                      "hours": a.get("hours"), "state": state, "seconds_left": secs,
                      "fed_count": r["fed_count"] or 0})
    return {"slots": slots, "animals": _animals_public(), "n_slots": n_slots,
            "sub": _is_sub(user), "golden_pct": int(GOLDEN_CHANCE * 100), "golden_mult": GOLDEN_MULT}


def buy(conn, user, animal_key: str) -> dict:
    """Kup zvíře do nejnižšího prázdného slotu (sink). Nové zvíře je HLADOVÉ (musíš nakrmit)."""
    from .deps import try_debit
    a = _BY_KEY.get(animal_key)
    if not a:
        return {"ok": False, "error": "Neznámé zvíře."}
    n = _n_slots(conn, user)
    occupied = {r["slot"] for r in conn.execute("SELECT slot FROM farm_animals WHERE user_id = ?", (user["id"],))}
    empty = [s for s in range(n) if s not in occupied]
    if not empty:
        return {"ok": False, "error": "Plný statek – nemáš volný slot. 🚜"}
    slot = empty[0]
    if not try_debit(conn, user["id"], a["cost"], f"Statek: koupě {a['name']} {a['icon']}"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({a['cost']})."}
    try:
        conn.execute(
            "INSERT INTO farm_animals (user_id, slot, animal_key, ready_at, fed_count, bought_at) "
            "VALUES (?, ?, ?, '', 0, ?)", (user["id"], slot, animal_key, now_iso()))
        conn.commit()
    except sqlite3.IntegrityError:                          # slot mezitím obsazen (souběh) → vrať cenu
        from .deps import add_points
        conn.rollback()
        add_points(conn, user["id"], a["cost"], "Vrácení za zvíře (souběh)", xp=False)
        conn.commit()
        return {"ok": False, "error": "Slot se mezitím obsadil. Zkus znovu."}
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": a["name"], "icon": a["icon"], "slot": slot}


def feed(conn, user, slot: int) -> dict:
    """Nakrm hladové zvíře (sink) → spustí produkční cyklus (ready_at = teď + hodiny)."""
    from .deps import try_debit
    r = conn.execute("SELECT * FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný slot."}
    if r["ready_at"]:
        return {"ok": False, "error": "Zvíře už produkuje (nebo má hotovo k sebrání). 🐔"}
    a = _BY_KEY.get(r["animal_key"], {})
    if not try_debit(conn, user["id"], a.get("feed", 0), f"Statek: krmivo {a.get('name', '?')} 🌾"):
        return {"ok": False, "error": f"Nemáš dost sedláků na krmivo ({a.get('feed', 0)})."}
    ready = (datetime.now(timezone.utc) + timedelta(hours=a.get("hours", 1))).isoformat()
    conn.execute("UPDATE farm_animals SET ready_at = ?, fed_count = fed_count + 1 WHERE user_id = ? AND slot = ?",
                 (ready, user["id"], slot))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": a.get("name"), "hours": a.get("hours")}


def _award_product(conn, user_id, a):
    """Připíše produkt: base sedláci + XP (reason 'Statek: …' → garden XP bucket). ZLATÝ = ×GOLDEN_MULT
    sedláci ale XP jen z base (golden bonus xp=False, jako zahrádka). Vrací (reward, golden)."""
    from .deps import add_points
    base = a["reward"]
    golden = random.random() < GOLDEN_CHANCE
    add_points(conn, user_id, base, f"Statek: {a['product']} {a['pico']}")              # base + XP
    if golden:
        add_points(conn, user_id, base * (GOLDEN_MULT - 1),
                   f"Statek: zlaté {a['product']} 🌟", xp=False)                          # extra sedláci BEZ XP
    return base * (GOLDEN_MULT if golden else 1), golden


def collect(conn, user, slot: int) -> dict:
    """Seber hotový produkt → odměna + zvíře zhladoví (ready_at='' → musíš zas nakrmit). Atomicky."""
    r = conn.execute("SELECT * FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný slot."}
    if not r["ready_at"]:
        return {"ok": False, "error": "Zvíře má hlad – nejdřív nakrm. 🌾"}
    if r["ready_at"] > now_iso():
        return {"ok": False, "error": "Ještě se nevyprodukovalo. ⏳"}
    a = _BY_KEY.get(r["animal_key"], {})
    # ATOMICKY: produkt sebere JEN request, který reálně přepnul ready_at→'' (rowcount==1) → anti-double.
    if conn.execute("UPDATE farm_animals SET ready_at = '' WHERE user_id = ? AND slot = ? AND ready_at = ?",
                    (user["id"], slot, r["ready_at"])).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Už sebráno. 🥚"}
    reward, golden = _award_product(conn, user["id"], a)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "golden": golden, "balance": bal,
            "name": a.get("name"), "product": a.get("product"), "pico": a.get("pico")}


def collect_all(conn, user) -> dict:
    """Sebere všechny hotové produkty naráz (atomicky per zvíře). Vrací počet, výnos, kolik zlatých."""
    ready = conn.execute("SELECT slot, animal_key, ready_at FROM farm_animals "
                         "WHERE user_id = ? AND ready_at != '' AND ready_at <= ?",
                         (user["id"], now_iso())).fetchall()
    total = count = golds = 0
    for r in ready:
        a = _BY_KEY.get(r["animal_key"], {})
        if conn.execute("UPDATE farm_animals SET ready_at = '' WHERE user_id = ? AND slot = ? AND ready_at = ?",
                        (user["id"], r["slot"], r["ready_at"])).rowcount != 1:
            continue
        reward, golden = _award_product(conn, user["id"], a)
        total += reward; count += 1; golds += 1 if golden else 0
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "count": count, "total": total, "golden": golds, "balance": bal}
