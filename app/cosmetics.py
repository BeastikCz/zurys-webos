"""Kosmetika: barvy nicku, rámečky avataru, profil bannery.

Sink na sedláky + status: koupíš za body (NEvratné), vlastníš navždy, nasazené si
přepínáš (1 aktivní na slot: name/frame/banner). Vizuál = CSS třída (`cls`) v styles.css,
takže i animované efekty jdou bez injektování stylů. Katalog v kódu (jako achievementy/
changelog → přidat kousek = řádek + deploy).

Ceny jsou kalibrované na ekonomiku (oběh ~2,7M, ~1000 uživatelů, top zůstatky ~340k):
  * 800–3 000  = dostupné skoro každému → vtáhne masu do utrácení
  * 6 000–18 000 = za týden+ grindu
  * 25 000–60 000 = pro nejbohatší → pálí ty velké hromady (hlavní sink efekt)
`sub=True` = jen pro suby (motivace subnout). `rarity` jen pro barevné štítky v UI.
"""
from .db import now_iso

# typ: name | frame | banner
CATALOG = [
    # ---- Barvy nicku ----
    {"key": "name_blue",    "type": "name",   "name": "Mil-Spec modrá",     "cost": 1000,  "rarity": "milspec",    "sub": False, "cls": "cn-blue"},
    {"key": "name_purple",  "type": "name",   "name": "Restricted fialová", "cost": 3000,  "rarity": "restricted", "sub": False, "cls": "cn-purple"},
    {"key": "name_pink",    "type": "name",   "name": "Classified růžová",  "cost": 6000,  "rarity": "classified", "sub": False, "cls": "cn-pink"},
    {"key": "name_emerald", "type": "name",   "name": "Smaragd",            "cost": 8000,  "rarity": "classified", "sub": True,  "cls": "cn-emerald"},
    {"key": "name_red",     "type": "name",   "name": "Covert červená",     "cost": 14000, "rarity": "covert",     "sub": False, "cls": "cn-red"},
    {"key": "name_gold",    "type": "name",   "name": "Contraband zlatá",   "cost": 30000, "rarity": "contraband", "sub": False, "cls": "cn-gold"},
    {"key": "name_rainbow", "type": "name",   "name": "Rainbow",            "cost": 60000, "rarity": "legendary",  "sub": False, "cls": "cn-rainbow"},
    # ---- Rámečky avataru ----
    {"key": "frame_bronze",  "type": "frame", "name": "Bronz",              "cost": 800,   "rarity": "milspec",    "sub": False, "cls": "cf-bronze"},
    {"key": "frame_silver",  "type": "frame", "name": "Stříbro",            "cost": 2500,  "rarity": "restricted", "sub": False, "cls": "cf-silver"},
    {"key": "frame_gold",    "type": "frame", "name": "Zlato",              "cost": 8000,  "rarity": "classified", "sub": False, "cls": "cf-gold"},
    {"key": "frame_emerald", "type": "frame", "name": "Smaragd prsten",     "cost": 10000, "rarity": "classified", "sub": True,  "cls": "cf-emerald"},
    {"key": "frame_neon",    "type": "frame", "name": "Neon puls",          "cost": 18000, "rarity": "covert",     "sub": False, "cls": "cf-neon"},
    {"key": "frame_fire",    "type": "frame", "name": "Rotující oheň",      "cost": 45000, "rarity": "legendary",  "sub": False, "cls": "cf-fire"},
    # ---- Profil bannery ----
    {"key": "banner_midnight", "type": "banner", "name": "Půlnoc",         "cost": 4000,  "rarity": "restricted", "sub": False, "cls": "cb-midnight"},
    {"key": "banner_aurora",   "type": "banner", "name": "Aurora",         "cost": 9000,  "rarity": "classified", "sub": False, "cls": "cb-aurora"},
    {"key": "banner_wave",     "type": "banner", "name": "Smaragdová vlna","cost": 12000, "rarity": "classified", "sub": True,  "cls": "cb-wave"},
    {"key": "banner_gold",     "type": "banner", "name": "Zlatá záře",     "cost": 25000, "rarity": "contraband", "sub": False, "cls": "cb-gold"},
    {"key": "banner_inferno",  "type": "banner", "name": "Inferno",        "cost": 38000, "rarity": "legendary",  "sub": False, "cls": "cb-inferno"},
]
_BY_KEY = {c["key"]: c for c in CATALOG}
_SLOT_COL = {"name": "cos_name", "frame": "cos_frame", "banner": "cos_banner"}


def get(key):
    return _BY_KEY.get(key)


def _equipped_keys(user_row) -> dict:
    """Nasazené klíče ze sloupců users (cos_name/cos_frame/cos_banner)."""
    out = {}
    for slot, col in _SLOT_COL.items():
        try:
            out[slot] = user_row[col]
        except (KeyError, IndexError, TypeError):
            out[slot] = None
    return out


def resolve(user_row) -> dict:
    """Z user řádku → CSS třídy nasazené kosmetiky pro render. Čistá (jen lookup), bez DB.
    Vrací {name, frame, banner} = CSS třída nebo prázdný řetězec."""
    keys = _equipped_keys(user_row)
    out = {"name": "", "frame": "", "banner": ""}
    for slot, key in keys.items():
        item = _BY_KEY.get(key) if key else None
        if item:
            out[slot] = item["cls"]
    return out


def list_for_user(conn, user_row) -> dict:
    """Katalog + stav pro uživatele (vlastněno / nasazeno)."""
    uid = user_row["id"]
    owned = {r["item_key"] for r in conn.execute(
        "SELECT item_key FROM cosmetic_owns WHERE user_id = ?", (uid,))}
    eq = _equipped_keys(user_row)
    items = []
    for c in CATALOG:
        items.append({
            "key": c["key"], "type": c["type"], "name": c["name"], "cost": c["cost"],
            "rarity": c["rarity"], "sub": c["sub"], "cls": c["cls"],
            "owned": c["key"] in owned,
            "equipped": eq.get(c["type"]) == c["key"],
        })
    return {"items": items, "balance": user_row["points"]}


def buy(conn, user_row, key: str) -> dict:
    """Koupí kosmetiku (atomický odečet). Vyhodí ValueError s českou hláškou při chybě."""
    item = _BY_KEY.get(key)
    if not item:
        raise ValueError("Tahle kosmetika neexistuje.")
    uid = user_row["id"]
    if conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id = ? AND item_key = ?",
                    (uid, key)).fetchone():
        raise ValueError("Tuhle kosmetiku už máš. ✓")
    is_admin = (user_row["role"] == "admin")
    is_sub = bool(user_row["is_sub"]) if "is_sub" in user_row.keys() else False
    if item["sub"] and not (is_sub or is_admin):
        raise ValueError("Tahle kosmetika je jen pro suby. 💜")
    from .deps import try_debit                                  # lazy (cyklický import)
    if not try_debit(conn, uid, item["cost"], f"Kosmetika: {item['name']} 🎨"):
        raise ValueError(f"Nemáš dost sedláků (stojí {item['cost']}).")
    conn.execute("INSERT INTO cosmetic_owns (user_id, item_key, acquired_at) VALUES (?,?,?)",
                 (uid, key, now_iso()))
    conn.commit()
    return item


def equip(conn, user_row, key: str) -> dict:
    """Nasadí kosmetiku do jejího slotu. Když je už nasazená → sundá (toggle). Musíš ji vlastnit."""
    item = _BY_KEY.get(key)
    if not item:
        raise ValueError("Tahle kosmetika neexistuje.")
    uid = user_row["id"]
    if not conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id = ? AND item_key = ?",
                        (uid, key)).fetchone():
        raise ValueError("Tuhle kosmetiku nevlastníš.")
    col = _SLOT_COL[item["type"]]
    try:
        current = user_row[col]
    except (KeyError, IndexError, TypeError):
        current = None
    newval = None if current == key else key                    # toggle
    conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (newval, uid))   # col z whitelistu
    conn.commit()
    return {"type": item["type"], "equipped_key": newval, "cls": item["cls"] if newval else ""}
