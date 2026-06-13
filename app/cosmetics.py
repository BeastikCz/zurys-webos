"""Kosmetika: barvy nicku, rámečky avataru, profil bannery.

Sink na sedláky + status: koupíš za body (NEvratné), vlastníš navždy, nasazené si
přepínáš (1 aktivní na slot: name/frame/banner). Vizuál = CSS třída (`cls`) v styles.css,
takže i animované efekty jdou bez injektování stylů. Katalog v kódu (jako achievementy/
changelog → přidat kousek = řádek + deploy).

Ceny kalibrované na REÁLNÁ data (2026-06-11: oběh 2,73M, 966 aktivních, medián zůstatku 118,
p99 57k, max 346k; rozložení extrémně nerovné – špička drží 100–346k hromady):
  * 800–2 500   = dostupné nejaktivnějším (~1/3 aktivních) → masová adopce, pálí malé hromádky
  * 6 000–22 000 = za týden+ grindu (top ~5–10 %)
  * 50 000–80 000 = jen pro velryby (top ~1 %) → drancuje obří hromady (hlavní sink; velryby je už berou)
`sub=True` = jen pro suby (motivace subnout). `rarity` jen pro barevné štítky v UI.
"""
from .db import now_iso

# typ: name | frame | banner
CATALOG = [
    # ---- Barvy nicku (cenově seřazené) ----
    {"key": "name_blue",    "type": "name",   "name": "Mil-Spec modrá",     "cost": 1000,  "rarity": "milspec",    "sub": False, "cls": "cn-blue"},
    {"key": "name_cyan",    "type": "name",   "name": "Tyrkysová",          "cost": 1500,  "rarity": "restricted", "sub": False, "cls": "cn-cyan"},
    {"key": "name_lime",    "type": "name",   "name": "Limetková",          "cost": 2000,  "rarity": "restricted", "sub": False, "cls": "cn-lime"},
    {"key": "name_purple",  "type": "name",   "name": "Restricted fialová", "cost": 2500,  "rarity": "restricted", "sub": False, "cls": "cn-purple"},
    {"key": "name_orange",  "type": "name",   "name": "Oranžová",           "cost": 3500,  "rarity": "classified", "sub": False, "cls": "cn-orange"},
    {"key": "name_crimson", "type": "name",   "name": "Karmínová",          "cost": 5000,  "rarity": "classified", "sub": False, "cls": "cn-crimson"},
    {"key": "name_pink",    "type": "name",   "name": "Classified růžová",  "cost": 6000,  "rarity": "classified", "sub": False, "cls": "cn-pink"},
    {"key": "name_emerald", "type": "name",   "name": "Smaragd",            "cost": 8000,  "rarity": "classified", "sub": True,  "cls": "cn-emerald"},
    {"key": "name_sunset",  "type": "name",   "name": "Západ slunce",       "cost": 9000,  "rarity": "classified", "sub": False, "cls": "cn-sunset"},
    {"key": "name_ice",     "type": "name",   "name": "Led",                "cost": 12000, "rarity": "covert",     "sub": False, "cls": "cn-ice"},
    {"key": "name_toxic",   "type": "name",   "name": "Toxic",              "cost": 14000, "rarity": "covert",     "sub": False, "cls": "cn-toxic"},
    {"key": "name_red",     "type": "name",   "name": "Covert červená",     "cost": 16000, "rarity": "covert",     "sub": False, "cls": "cn-red"},
    {"key": "name_galaxy",  "type": "name",   "name": "Galaxie",            "cost": 30000, "rarity": "contraband", "sub": False, "cls": "cn-galaxy"},
    {"key": "name_lava",    "type": "name",   "name": "Láva",               "cost": 45000, "rarity": "contraband", "sub": False, "cls": "cn-lava"},
    {"key": "name_gold",    "type": "name",   "name": "Contraband zlatá",   "cost": 50000, "rarity": "contraband", "sub": False, "cls": "cn-gold"},
    {"key": "name_holo",    "type": "name",   "name": "Holografická",       "cost": 70000, "rarity": "legendary",  "sub": False, "cls": "cn-holo"},
    {"key": "name_rainbow", "type": "name",   "name": "Rainbow",            "cost": 80000, "rarity": "legendary",  "sub": False, "cls": "cn-rainbow"},
    # ---- Rámečky avataru (cenově seřazené) ----
    {"key": "frame_bronze",   "type": "frame", "name": "Bronz",            "cost": 800,   "rarity": "milspec",    "sub": False, "cls": "cf-bronze"},
    {"key": "frame_silver",   "type": "frame", "name": "Stříbro",          "cost": 2500,  "rarity": "restricted", "sub": False, "cls": "cf-silver"},
    {"key": "frame_ruby",     "type": "frame", "name": "Rubín",            "cost": 3500,  "rarity": "restricted", "sub": False, "cls": "cf-ruby"},
    {"key": "frame_sapphire", "type": "frame", "name": "Safír",            "cost": 5000,  "rarity": "classified", "sub": False, "cls": "cf-sapphire"},
    {"key": "frame_gold",     "type": "frame", "name": "Zlato",            "cost": 8000,  "rarity": "classified", "sub": False, "cls": "cf-gold"},
    {"key": "frame_amethyst", "type": "frame", "name": "Ametyst",          "cost": 8000,  "rarity": "classified", "sub": False, "cls": "cf-amethyst"},
    {"key": "frame_emerald",  "type": "frame", "name": "Smaragd prsten",   "cost": 10000, "rarity": "classified", "sub": True,  "cls": "cf-emerald"},
    {"key": "frame_ice",      "type": "frame", "name": "Ledový prsten",    "cost": 12000, "rarity": "covert",     "sub": False, "cls": "cf-ice"},
    {"key": "frame_neon",     "type": "frame", "name": "Neon puls",        "cost": 22000, "rarity": "covert",     "sub": False, "cls": "cf-neon"},
    {"key": "frame_rainbow",  "type": "frame", "name": "Duhový prsten",    "cost": 55000, "rarity": "legendary",  "sub": False, "cls": "cf-rainbow"},
    {"key": "frame_fire",     "type": "frame", "name": "Rotující oheň",    "cost": 60000, "rarity": "legendary",  "sub": False, "cls": "cf-fire"},
]

# Zrušené kousky (v1: profil bannery vypadaly špatně) → cena, pro JEDNORÁZOVÝ refund komu je
# koupil. Drží se tu mimo CATALOG, aby šlo vrátit sedláky i po odebrání z nabídky.
REFUND_REMOVED = {
    "banner_midnight": 4000, "banner_aurora": 9000, "banner_wave": 12000,
    "banner_gold": 25000, "banner_inferno": 38000,
}
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


def refund_removed(conn) -> int:
    """Jednorázově: vrátí sedláky za zrušené kousky (bannery), smaže je z vlastnictví a sundá
    nasazené. Idempotentní – po prvním běhu už řádky nejsou, takže nic nevrátí. Vrací počet refundů."""
    from .deps import add_points
    n = 0
    for key, cost in REFUND_REMOVED.items():
        for r in conn.execute("SELECT user_id FROM cosmetic_owns WHERE item_key = ?", (key,)).fetchall():
            add_points(conn, r["user_id"], cost, f"Refund zrušené kosmetiky ({key}) 🔁")
            n += 1
        conn.execute("DELETE FROM cosmetic_owns WHERE item_key = ?", (key,))
    conn.execute("UPDATE users SET cos_banner = NULL WHERE cos_banner IS NOT NULL")
    return n
