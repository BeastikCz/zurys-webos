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
import json
import random
import re
import sqlite3
from datetime import datetime, timezone, timedelta

from .db import now_iso, local_now, LOCAL_TZ

BASE_SLOTS = 2
FOX_CHANCE = 0.20              # šance 1×/den, že se objeví liška (jen když má user co ukrást)
FOX_RANSOM_PCT = 0.5           # výkupné = 50 % hodnoty ohroženého produktu (vždy se vyplatí zaplatit)…
FOX_RANSOM_MIN, FOX_RANSOM_MAX = 50, 200   # …sevřeno, ať to není nula ani drakonické
FOX_RANSOM = 200               # fallback pro pending lišky z doby fixního výkupného

STARTER_COST = 500             # první zvíře vůbec (slepice) za zlomek ceny – onboarding nováčka
FARM_DAILY_FULL = 20000        # základ: sedláci z produkce/den v plné výši; nad strop jen FARM_SOFT_RATE
FARM_DAILY_PER_BARN = 5000     # +strop za každý level stodoly nad 1 (sink kupuje i denní kapacitu, lvl 5 = 40k)
FARM_SOFT_RATE = 0.25          # (XP jde ze snížené částky taky – měkký strop, ne tvrdý blok)

# Zakázky: denní kontrakt z vlastněných druhů („dodej 3× vejce + 2× mléko"). Odměna sedláci BEZ XP,
# strop pod quest kotvou (750/den), ať se faucety nedublují.
CONTRACT_SPECIES = 2           # kolik druhů v zakázce (max, dle vlastněných)
CONTRACT_PER_DAY = {"chicken": 3, "goat": 2, "sheep": 2, "cow": 1, "unicorn": 1}   # ks ≈ co stihne 1 zvíře/den
CONTRACT_REWARD_PCT = 0.5      # odměna = 50 % hodnoty dodaných produktů…
CONTRACT_MIN, CONTRACT_MAX = 300, 700   # …sevřeno do pásma

# Prestige stodoly: users.barn_level 1..5, každý level +1 slot. Velký sink pro bohaté účty.
BARN_MAX = 5
BARN_COSTS = {2: 50000, 3: 150000, 4: 400000, 5: 1000000}   # cena upgradu NA level
SUB_SLOTS = 1
PATRON_SLOT_GIFTS = 5         # 5 darovaných subů v sezóně = 1 patron slot
PATRON_FOX_GIFTS = 15         # mecenáš sezóny má ochranu před liškou
TURBO_SPEED_MULT = 2.0        # jeden turbo žeton zrychlí jediný příští cyklus
TURBO_MAX_STORED = 3
TURBO_TTL_DAYS = 7
GOLDEN_CHANCE = 0.03
GOLDEN_EVENT_CHANCE = 0.10     # „Zlatá horečka" (launch event): šance na zlatý produkt během okna
GOLDEN_MULT = 3
PRODUCT_PICOS = ("🥚", "🥛", "🧶", "🧀", "💩", "🌟")   # markery produkce v points_log (žebříček)
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


def _barn_level(user) -> int:
    try:
        return max(1, min(BARN_MAX, user["barn_level"] or 1))
    except (KeyError, IndexError, TypeError):
        return 1


def _patron_status(conn, uid: int) -> dict:
    """Sezónní patronství z potvrzených Kick gift-sub logů."""
    now = local_now()
    season = now.strftime("%Y-%m")
    start = datetime(now.year, now.month, 1, tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()
    gifts = 0
    for row in conn.execute(
        "SELECT reason FROM points_log WHERE user_id = ? AND created_at >= ? "
        "AND LOWER(reason) LIKE '%kick gift sub%' AND LOWER(reason) NOT LIKE '%příjemce%'",
        (uid, start),
    ):
        match = re.search(r"[×x]\s*(\d+)", row["reason"] or "", re.IGNORECASE)
        gifts += int(match.group(1)) if match else 1

    if gifts >= 50:
        title = "Legenda farmy"
        next_at = None
    elif gifts >= 30:
        title = "Patron farmy"
        next_at = 50
    elif gifts >= PATRON_FOX_GIFTS:
        title = "Mecenáš farmy"
        next_at = 30
    elif gifts >= PATRON_SLOT_GIFTS:
        title = "Patron statku"
        next_at = PATRON_FOX_GIFTS
    elif gifts:
        title = "Dárce farmy"
        next_at = PATRON_SLOT_GIFTS
    else:
        title = None
        next_at = 1
    return {"season": season, "gifts": gifts, "title": title, "next_at": next_at,
            "slot_bonus": gifts >= PATRON_SLOT_GIFTS,
            "fox_guard": gifts >= PATRON_FOX_GIFTS}


def _n_slots(conn, user, patron: dict | None = None) -> int:
    patron = patron or _patron_status(conn, user["id"])
    return BASE_SLOTS + (SUB_SLOTS if _is_sub(user) else 0) + (_barn_level(user) - 1) + int(patron["slot_bonus"])


def _turbo_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=TURBO_TTL_DAYS)).isoformat()


def _turbo_status(conn, uid: int) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS count, MIN(created_at) AS oldest FROM farm_turbo_tokens "
        "WHERE user_id = ? AND used_at IS NULL AND created_at >= ?", (uid, _turbo_cutoff())
    ).fetchone()
    return {"count": row["count"], "max": TURBO_MAX_STORED, "oldest": row["oldest"]}


def grant_turbo_tokens(conn, uid: int, count: int) -> int:
    """Potvrzené gift suby dají max. tři nevyužité turbo žetony; neakumulují se donekonečna."""
    if count <= 0:
        return 0
    available = _turbo_status(conn, uid)["count"]
    granted = min(count, max(0, TURBO_MAX_STORED - available))
    if granted:
        conn.executemany("INSERT INTO farm_turbo_tokens (user_id, created_at) VALUES (?, ?)",
                         [(uid, now_iso())] * granted)
    return granted


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
    starter = _starter_eligible(conn, user["id"])
    out = []
    for a in ANIMALS:
        out.append({"key": a["key"], "icon": a["icon"], "name": a["name"],
                    "cost": STARTER_COST if starter and a["key"] == "chicken" else a["cost"],
                    "starter": starter and a["key"] == "chicken",
                    "utility": a.get("utility", False), "prod_bonus": int(a.get("prod_bonus", 0) * 100),
                    "sub_only": a.get("sub_only", False), "locked": a.get("sub_only", False) and not sub,
                    "owned": a["key"] in owned,
                    "feed": a.get("feed", 0), "hours": a.get("hours", 0),
                    "reward": a.get("reward", 0), "product": a.get("product", ""), "pico": a.get("pico", "")})
    return out


def _owns_dog(conn, uid: int) -> bool:
    return conn.execute("SELECT 1 FROM farm_animals WHERE user_id = ? AND animal_key = 'horse'",
                        (uid,)).fetchone() is not None


def _fox_pending(conn, uid: int) -> dict | None:
    """Vrátí nevyřešenou lišku {slot, ready_at}, nebo None. Zastaralou (zvíře pryč / produkt jiný) smaže."""
    r = conn.execute("SELECT farm_fox FROM users WHERE id = ?", (uid,)).fetchone()
    raw = (r["farm_fox"] if r else None) or ""
    if not raw:
        return None
    try:
        fox = json.loads(raw)
    except ValueError:
        fox = None
    if fox:
        a = conn.execute("SELECT ready_at FROM farm_animals WHERE user_id = ? AND slot = ?",
                         (uid, fox.get("slot", -1))).fetchone()
        if a and a["ready_at"] == fox.get("ready_at"):
            return fox
    conn.execute("UPDATE users SET farm_fox = '' WHERE id = ?", (uid,))
    return None


def _roll_fox(conn, user) -> None:
    """1× denně: šance na lišku, která si brousí zuby na jeden rozdělaný/hotový produkt.
    Pes 🐕 ji odežene zdarma (notif) – bez psa vzniká pending event (výkupné / ztráta produktu)."""
    uid = user["id"]
    today = now_iso()[:10]
    r = conn.execute("SELECT farm_fox_day FROM users WHERE id = ?", (uid,)).fetchone()
    if not r or (r["farm_fox_day"] or "") == today:
        return
    conn.execute("UPDATE users SET farm_fox_day = ? WHERE id = ?", (today, uid))
    if _fox_pending(conn, uid) or random.random() >= FOX_CHANCE:
        conn.commit()
        return
    targets = [t for t in conn.execute(
        "SELECT slot, ready_at, animal_key, fed_count FROM farm_animals WHERE user_id = ? AND ready_at != ''", (uid,))
        if not _BY_KEY.get(t["animal_key"], {}).get("utility")]
    if not targets:
        conn.commit()
        return
    from .deps import notify
    if _owns_dog(conn, uid) or _patron_status(conn, uid)["fox_guard"]:
        notify(conn, uid, "🐕", "Pes odehnal lišku!",
               "K statku se plížila liška 🦊 – ochránce statku ji odehnal. Dobrá investice! 🐾", "#/statek")
        conn.commit()
        return
    t = random.choice(targets)
    a = _BY_KEY.get(t["animal_key"], {})
    value = _reward_at(a, _level(t["fed_count"]), _prod_bonus(conn, uid))
    ransom = max(FOX_RANSOM_MIN, min(FOX_RANSOM_MAX, int(value * FOX_RANSOM_PCT)))
    conn.execute("UPDATE users SET farm_fox = ? WHERE id = ?",
                 (json.dumps({"slot": t["slot"], "ready_at": t["ready_at"], "ransom": ransom}), uid))
    notify(conn, uid, "🦊", "Liška na statku!",
           f"Liška si brousí zuby na tvůj produkt. Zaplať výkupné {ransom} sedláků, nebo o něj přijdeš! 🚜",
           "#/statek")
    conn.commit()


def resolve_fox(conn, user, pay: bool) -> dict:
    """Vyřeší pending lišku: pay=True → výkupné (sink), produkt zůstává. pay=False → produkt fuč, zvíře zhladoví."""
    from .deps import try_debit
    fox = _fox_pending(conn, user["id"])
    if not fox:
        conn.commit()
        return {"ok": False, "error": "Žádná liška tu není. 🦊"}
    ransom = fox.get("ransom", FOX_RANSOM)
    if pay:
        if not try_debit(conn, user["id"], ransom, "Statek: výkupné lišce 🦊"):
            return {"ok": False, "error": f"Nemáš na výkupné ({ransom} sedláků)."}
        conn.execute("UPDATE users SET farm_fox = '' WHERE id = ?", (user["id"],))
        conn.commit()
        paid = True
    else:
        conn.execute("UPDATE farm_animals SET ready_at = '' WHERE user_id = ? AND slot = ? AND ready_at = ?",
                     (user["id"], fox["slot"], fox["ready_at"]))
        conn.execute("UPDATE users SET farm_fox = '' WHERE id = ?", (user["id"],))
        conn.commit()
        paid = False
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "paid": paid, "balance": bal, "ransom": ransom}


def golden_event_until(conn) -> str:
    """ISO konec „Zlaté horečky" (''/minulost = neaktivní). Nastavuje admin endpoint."""
    from .db import get_setting
    return get_setting(conn, "farm_golden_until", "") or ""


def _golden_chance(conn) -> float:
    return GOLDEN_EVENT_CHANCE if golden_event_until(conn) > now_iso() else GOLDEN_CHANCE


def golden_event_start(conn, days: int) -> dict:
    """Spustí „Zlatou horečku" na N dní (šance na zlatý produkt 3 % → 10 %) + oznámí v chatu."""
    import threading
    import traceback
    from .db import set_setting, get_conn
    until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    set_setting(conn, "farm_golden_until", until)
    conn.commit()

    def _send():   # Kick HTTP mimo request thread (single-writer SQLite – viz live_events)
        try:
            c = get_conn()
            try:
                from . import kickbot
                kickbot.send_message(
                    c, f"🌟 ZLATÁ HOREČKA NA STATKU! {days} dní mají zlaté produkty "
                       f"{int(GOLDEN_EVENT_CHANCE * 100)}% šanci (místo {int(GOLDEN_CHANCE * 100)} %) = ×{GOLDEN_MULT} sedláci. "
                       f"Kup zvíře na zurys.live a farmi! 🚜", kind="farmevent")
            finally:
                c.close()
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_send, name="webos-farm-event", daemon=True).start()
    return {"until": until, "chance_pct": int(GOLDEN_EVENT_CHANCE * 100)}


def golden_event_stop(conn) -> dict:
    from .db import set_setting
    set_setting(conn, "farm_golden_until", "")
    conn.commit()
    return {"until": ""}


def _starter_eligible(conn, uid: int) -> bool:
    """Nováček bez jediného zvířete v historii (sbírka prázdná) → první slepice za STARTER_COST."""
    return conn.execute("SELECT 1 FROM farm_collection WHERE user_id = ?", (uid,)).fetchone() is None


def _farm_today(conn, uid: int) -> int:
    """Sedláci z produkce připsaní dnes (pro měkký denní strop)."""
    r = conn.execute("SELECT farm_today, farm_day FROM users WHERE id = ?", (uid,)).fetchone()
    if not r or (r["farm_day"] or "") != now_iso()[:10]:
        return 0
    return r["farm_today"] or 0


def _daily_full(conn, uid: int) -> int:
    """Denní strop plné sazby: základ + bonus za level stodoly (sink kupuje i kapacitu, ne jen slot)."""
    r = conn.execute("SELECT barn_level FROM users WHERE id = ?", (uid,)).fetchone()
    lvl = max(1, min(BARN_MAX, ((r["barn_level"] if r else 1) or 1)))
    return FARM_DAILY_FULL + FARM_DAILY_PER_BARN * (lvl - 1)


def _farm_today_add(conn, uid: int, amount: int) -> None:
    today = now_iso()[:10]
    conn.execute("UPDATE users SET farm_today = CASE WHEN COALESCE(farm_day,'') = ? "
                 "THEN COALESCE(farm_today,0) + ? ELSE ? END, farm_day = ? WHERE id = ?",
                 (today, amount, amount, today, uid))


def _contract_row(conn, uid: int):
    return conn.execute("SELECT * FROM farm_contracts WHERE user_id = ? AND day = ?",
                        (uid, now_iso()[:10])).fetchone()


def _ensure_contract(conn, user) -> None:
    """Založí dnešní zakázku z VLASTNĚNÝCH produkčních druhů (deterministicky den+user).
    Bez produkčního zvířete zakázka není (objeví se po první koupi)."""
    uid = user["id"]
    if _contract_row(conn, uid):
        return
    owned = sorted({r["animal_key"] for r in conn.execute(
        "SELECT animal_key FROM farm_animals WHERE user_id = ?", (uid,))
        if not _BY_KEY.get(r["animal_key"], {}).get("utility")})
    if not owned:
        return
    day = now_iso()[:10]
    rng = random.Random(f"{day}:{uid}")
    species = rng.sample(owned, min(CONTRACT_SPECIES, len(owned)))
    need = {k: CONTRACT_PER_DAY.get(k, 1) for k in species}
    value = sum(n * _BY_KEY[k]["reward"] for k, n in need.items())
    reward = max(CONTRACT_MIN, min(CONTRACT_MAX, int(value * CONTRACT_REWARD_PCT)))
    conn.execute("INSERT OR IGNORE INTO farm_contracts (user_id, day, need, progress, reward, claimed) "
                 "VALUES (?,?,?,?,?,0)", (uid, day, json.dumps(need), "{}", reward))
    conn.commit()


def _contract_tick(conn, uid: int, animal_key: str) -> None:
    """Připíše sebraný produkt do dnešní zakázky (volá _award_product). Necommituje."""
    r = _contract_row(conn, uid)
    if not r or r["claimed"]:
        return
    need = json.loads(r["need"])
    if animal_key not in need:
        return
    prog = json.loads(r["progress"] or "{}")
    if prog.get(animal_key, 0) >= need[animal_key]:
        return
    prog[animal_key] = prog.get(animal_key, 0) + 1
    conn.execute("UPDATE farm_contracts SET progress = ? WHERE user_id = ? AND day = ?",
                 (json.dumps(prog), uid, r["day"]))


def _contract_public(conn, uid: int) -> dict | None:
    r = _contract_row(conn, uid)
    if not r:
        return None
    need = json.loads(r["need"])
    prog = json.loads(r["progress"] or "{}")
    items = [{"key": k, "icon": _BY_KEY[k]["icon"], "pico": _BY_KEY[k]["pico"],
              "product": _BY_KEY[k]["product"], "have": min(prog.get(k, 0), n), "goal": n}
             for k, n in need.items() if k in _BY_KEY]
    done = all(i["have"] >= i["goal"] for i in items)
    return {"items": items, "reward": r["reward"], "claimed": bool(r["claimed"]), "done": done}


def claim_contract(conn, user) -> dict:
    from .deps import add_points
    r = _contract_row(conn, user["id"])
    if not r:
        return {"ok": False, "error": "Dnes žádná zakázka není. 📋"}
    need, prog = json.loads(r["need"]), json.loads(r["progress"] or "{}")
    if not all(prog.get(k, 0) >= n for k, n in need.items()):
        return {"ok": False, "error": "Zakázka ještě není splněná. 📋"}
    if conn.execute("UPDATE farm_contracts SET claimed = 1 WHERE user_id = ? AND day = ? AND claimed = 0",
                    (user["id"], r["day"])).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Už vyzvednuto. ✅"}
    add_points(conn, user["id"], r["reward"], "Statek: zakázka splněna 📋", xp=False)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": r["reward"], "balance": bal}


def upgrade_barn(conn, user) -> dict:
    """Vylepší stodolu (velký sink) → +1 slot. Atomicky přes WHERE barn_level = starý."""
    from .deps import try_debit, add_points
    lvl = conn.execute("SELECT barn_level FROM users WHERE id = ?", (user["id"],)).fetchone()["barn_level"] or 1
    if lvl >= BARN_MAX:
        return {"ok": False, "error": "Stodola je na maximu. 🏆"}
    cost = BARN_COSTS[lvl + 1]
    if not try_debit(conn, user["id"], cost, f"Statek: stodola level {lvl + 1} 🏠"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({cost:,})".replace(",", " ") + "."}
    if conn.execute("UPDATE users SET barn_level = ? WHERE id = ? AND COALESCE(barn_level, 1) = ?",
                    (lvl + 1, user["id"], lvl)).rowcount != 1:
        add_points(conn, user["id"], cost, "Vrácení za stodolu (souběh)", xp=False)
        conn.commit()
        return {"ok": False, "error": "Souběh – zkus znovu."}
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "level": lvl + 1, "balance": bal}


def status(conn, user) -> dict:
    _roll_fox(conn, user)
    _ensure_contract(conn, user)
    fox = _fox_pending(conn, user["id"])
    now = datetime.now(timezone.utc)
    patron = _patron_status(conn, user["id"])
    active_slots = _n_slots(conn, user, patron)
    bonus = _prod_bonus(conn, user["id"])
    rows = {r["slot"]: r for r in conn.execute("SELECT * FROM farm_animals WHERE user_id = ?", (user["id"],))}
    n_slots = max(active_slots, max(rows, default=-1) + 1)  # po sezoně neschová zvíře v bývalém patron slotu
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
    return {"slots": slots, "animals": _animals_public(conn, user), "n_slots": n_slots,
            "active_slots": active_slots, "sub": _is_sub(user),
            "golden_pct": int(_golden_chance(conn) * 100), "golden_mult": GOLDEN_MULT,
            "golden_event": golden_event_until(conn) > now_iso(),
            "golden_event_until": golden_event_until(conn) if golden_event_until(conn) > now_iso() else "",
            "krmivo": _feed_stock(conn, user["id"]),
            "prod_bonus": int(bonus * 100),
            "patron": patron,
            "turbo": _turbo_status(conn, user["id"]),
            "fox": ({"slot": fox["slot"], "ransom": fox.get("ransom", FOX_RANSOM)} if fox else None),
            "farm_today": _farm_today(conn, user["id"]), "farm_daily_full": _daily_full(conn, user["id"]),
            "starter": _starter_eligible(conn, user["id"]), "starter_cost": STARTER_COST,
            "contract": _contract_public(conn, user["id"]),
            "barn": {"level": _barn_level(user), "max": BARN_MAX,
                     "next_cost": BARN_COSTS.get(_barn_level(user) + 1)},
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
    if len(occupied) >= n:  # sezónní patron slot mohl skončit; zvíře v něm nejdřív prodej
        return {"ok": False, "error": "Plný statek – prodej zvíře nebo si odemkni patron slot. 🚜"}
    empty = [s for s in range(n) if s not in occupied]
    if not empty:
        return {"ok": False, "error": "Plný statek – prodej zvíře nebo si jako sub odemkni víc slotů. 🚜"}
    slot = empty[0]
    # onboarding: úplně první zvíře (slepice) za zlomek ceny; prodej pak vrátí max +500 1×/účet – zanedbatelné
    cost = STARTER_COST if animal_key == "chicken" and _starter_eligible(conn, user["id"]) else a["cost"]
    if not try_debit(conn, user["id"], cost, f"Statek: koupě {a['name']} {a['icon']}"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({cost})."}
    try:
        conn.execute("SAVEPOINT farm_buy")
        conn.execute("INSERT INTO farm_animals (user_id, slot, animal_key, ready_at, fed_count, bought_at) "
                     "VALUES (?, ?, ?, '', 0, ?)", (user["id"], slot, animal_key, now_iso()))
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK TO SAVEPOINT farm_buy")
        conn.rollback()
        return {"ok": False, "error": "Slot se mezitím obsadil. Zkus znovu."}
    _note_collection(conn, user["id"], animal_key)
    # „stáj": druh už jsi trénoval → nové zvíře nastoupí s uloženým levelem (fed_count ze sbírky)
    stored = conn.execute("SELECT fed_count FROM farm_collection WHERE user_id = ? AND animal_key = ?",
                          (user["id"], animal_key)).fetchone()
    restored = (stored["fed_count"] if stored else 0) or 0
    if restored:
        conn.execute("UPDATE farm_animals SET fed_count = ? WHERE user_id = ? AND slot = ?",
                     (restored, user["id"], slot))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": a["name"], "icon": a["icon"], "slot": slot,
            "utility": a.get("utility", False), "level": _level(restored)}


def sell(conn, user, slot: int) -> dict:
    """Prodá zvíře ze slotu (vrátí část ceny, uvolní slot). Sbírku to NEodebere (druh zůstává nasbíraný)."""
    from .deps import add_points
    r = conn.execute("SELECT * FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný slot."}
    a = _BY_KEY.get(r["animal_key"], {})
    refund = int(round(a.get("cost", 0) * SELL_REFUND_PCT))
    # „stáj": ulož natrénovaný level druhu – při znovukoupení se obnoví (prodej nemaže progres)
    conn.execute("UPDATE farm_collection SET fed_count = MAX(COALESCE(fed_count, 0), ?) "
                 "WHERE user_id = ? AND animal_key = ?", (r["fed_count"] or 0, user["id"], r["animal_key"]))
    if conn.execute("DELETE FROM farm_animals WHERE user_id = ? AND slot = ?", (user["id"], slot)).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Už prodáno."}
    if refund > 0:
        add_points(conn, user["id"], refund, f"Statek: prodej {a.get('name', '?')} 💰", xp=False)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "refund": refund, "name": a.get("name")}


def feed(conn, user, slot: int, turbo: bool = False) -> dict:
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
    token = None
    if turbo:
        token = conn.execute(
            "SELECT id FROM farm_turbo_tokens WHERE user_id = ? AND used_at IS NULL AND created_at >= ? "
            "ORDER BY id LIMIT 1", (user["id"], _turbo_cutoff())
        ).fetchone()
        if not token:
            return {"ok": False, "error": "Nemáš žádný turbo žeton. ⚡"}
    used_krmivo = False
    if _feed_stock(conn, user["id"]) >= 1:                       # krmivo má přednost (zdarma, ze zahrádky)
        conn.execute("UPDATE users SET feed_stock = feed_stock - 1 WHERE id = ?", (user["id"],))
        used_krmivo = True
    elif not try_debit(conn, user["id"], a.get("feed", 0), f"Statek: krmivo {a.get('name', '?')} 🌾"):
        return {"ok": False, "error": f"Nemáš krmivo ani dost sedláků na krmení ({a.get('feed', 0)})."}
    fed_count = (r["fed_count"] or 0) + 1
    lvl_before, lvl_after = _level(r["fed_count"]), _level(fed_count)
    hours = _hours_at(a, lvl_after)
    if turbo:
        if conn.execute("UPDATE farm_turbo_tokens SET used_at = ? WHERE id = ? AND used_at IS NULL",
                        (now_iso(), token["id"])).rowcount != 1:
            conn.rollback()
            return {"ok": False, "error": "Turbo žeton mezitím použil jiný požadavek. Zkus to znovu."}
        hours /= TURBO_SPEED_MULT
    ready = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    conn.execute("UPDATE farm_animals SET ready_at = ?, fed_count = ?, notified = 0 WHERE user_id = ? AND slot = ?",
                 (ready, fed_count, user["id"], slot))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "name": a.get("name"), "used_krmivo": used_krmivo,
            "leveled_up": lvl_after > lvl_before, "level": lvl_after,
            "turbo": turbo, "turbo_left": _turbo_status(conn, user["id"])["count"]}


def feed_all(conn, user) -> dict:
    """Nakrmí všechna hladová produkční zvířata (krmivo přednostně, pak sedláci). Přeskočí, na co nejsou prostředky."""
    hungry = [r for r in conn.execute(
        "SELECT slot, animal_key FROM farm_animals WHERE user_id = ? AND ready_at = '' ORDER BY slot",
        (user["id"],)) if not _BY_KEY.get(r["animal_key"], {}).get("utility")]
    count = krmivo_used = 0
    for r in hungry:
        res = feed(conn, user, r["slot"])   # commituje per slot; nedostatek vrátí ok=False → skip
        if not res.get("ok"):
            continue
        count += 1
        if res["used_krmivo"]:
            krmivo_used += 1
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    skipped = len(hungry) - count
    return {"ok": True, "count": count, "krmivo_used": krmivo_used,
            "skipped": skipped, "balance": bal}


def _award_product(conn, user_id, a, level, bonus):
    from .deps import add_points
    base = _reward_at(a, level, bonus)
    # měkký denní strop (základ + stodola): nad něj jde jen zlomek (grinder ochrana faucetu)
    soft = _farm_today(conn, user_id) >= _daily_full(conn, user_id)
    paid = max(1, int(base * FARM_SOFT_RATE)) if soft else base
    golden = random.random() < _golden_chance(conn)
    add_points(conn, user_id, paid, f"Statek: {a['product']} {a['pico']}" + (" (nad denní strop)" if soft else ""))
    _farm_today_add(conn, user_id, paid)
    _contract_tick(conn, user_id, a["key"])
    if golden:
        add_points(conn, user_id, paid * (GOLDEN_MULT - 1), f"Statek: zlaté {a['product']} 🌟", xp=False)
    return paid * (GOLDEN_MULT if golden else 1), golden


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
    fox = _fox_pending(conn, user["id"])
    if fox and fox["slot"] == slot:
        return {"ok": False,
                "error": f"Na produkt číhá liška! 🦊 Zaplať výkupné ({fox.get('ransom', FOX_RANSOM)}), nebo jí ho nech."}
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
    fox = _fox_pending(conn, user["id"])
    ready = conn.execute("SELECT slot, animal_key, ready_at, fed_count FROM farm_animals "
                         "WHERE user_id = ? AND ready_at != '' AND ready_at <= ?",
                         (user["id"], now_iso())).fetchall()
    total = count = golds = 0
    for r in ready:
        a = _BY_KEY.get(r["animal_key"], {})
        if a.get("utility") or (fox and fox["slot"] == r["slot"]):
            continue
        if conn.execute("UPDATE farm_animals SET ready_at = '' WHERE user_id = ? AND slot = ? AND ready_at = ?",
                        (user["id"], r["slot"], r["ready_at"])).rowcount != 1:
            continue
        reward, golden = _award_product(conn, user["id"], a, _level(r["fed_count"]), bonus)
        total += reward; count += 1; golds += 1 if golden else 0
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "count": count, "total": total, "golden": golds, "balance": bal}
