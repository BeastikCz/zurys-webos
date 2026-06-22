"""Sdílená obchodní logika: validace a provedení nákupu (shop i košík)."""
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException

from .config import ROLE_ADMIN, ORDER_PENDING, UNLIMITED_STOCK, SKIN_TRADE_KEYWORDS
from .db import now_iso, get_setting, set_setting
from .deps import add_points, try_debit


def needs_trade_link(category) -> bool:
    """Vyžaduje tato kategorie odměny Steam trade link? (CS skiny – nože/rukavice/zbraně)"""
    c = (category or "").lower()
    return any(k in c for k in SKIN_TRADE_KEYWORDS)


def _happy_expired_off(conn) -> bool:
    """Časovač happy hour: když je nastavený konec (happy_until) a vypršel → vypni VŠE
    (sleva + 2× subs + časovač). Lazy auto-off (jako maintenance). True = právě/už vyprchal."""
    until = get_setting(conn, "happy_until", "") or ""
    if not until:
        return False
    try:
        if datetime.now(timezone.utc) >= datetime.fromisoformat(until):
            set_setting(conn, "shop_discount_pct", "0")
            set_setting(conn, "happy_sub_2x", "0")
            set_setting(conn, "happy_until", "")
            conn.commit()
            return True
    except (ValueError, TypeError):
        return False
    return False


def shop_discount_pct(conn) -> int:
    """Happy-hour sleva na nákupy (%). 0 = vypnuto. Volitelně jen když je live. Řídí admin.
    Aplikuje se SERVER-SIDE (validate + apply), ať se opravdu účtuje míň, ne jen zobrazí."""
    if _happy_expired_off(conn):
        return 0
    try:
        pct = int(get_setting(conn, "shop_discount_pct", "0") or "0")
    except (ValueError, TypeError):
        pct = 0
    if pct <= 0:
        return 0
    if get_setting(conn, "shop_discount_live_only", "0") == "1":
        from . import live
        if not live.is_live(conn):
            return 0
    return min(90, pct)


def disc_unit(cost: int, pct: int) -> int:
    """Cena za kus po slevě (zaokrouhleno)."""
    return round(cost * (100 - pct) / 100) if pct > 0 else cost


def sub_points_mult(conn) -> int:
    """Happy-hour násobič bodů za subs/gift subs: 2× když zapnuto (happy_sub_2x), jinak 1×.
    Sdílí přepínač „jen když live" se shop slevou (shop_discount_live_only). Řídí admin."""
    if _happy_expired_off(conn):
        return 1
    if get_setting(conn, "happy_sub_2x", "0") != "1":
        return 1
    if get_setting(conn, "shop_discount_live_only", "0") == "1":
        from . import live
        if not live.is_live(conn):
            return 1
    return 2


def _has_trade_link(user) -> bool:
    """Má uživatel vyplněný Steam trade link?"""
    try:
        return bool((user["steam_trade_url"] or "").strip()) if "steam_trade_url" in user.keys() else False
    except (TypeError, KeyError):
        return False


def _is_expired(ends_at) -> bool:
    """Vypršela platnost odměny? (ends_at je ISO string, prázdné = bez limitu)"""
    if not ends_at:
        return False
    try:
        dt = datetime.fromisoformat(str(ends_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def product_public(row: sqlite3.Row) -> dict:
    """Produkt do JSON odpovědi (booleany místo 0/1, doplněné příznaky)."""
    stock = row["stock"]
    keys = row.keys()
    sold = row["tickets_sold"] if "tickets_sold" in keys else None
    return {
        "id": row["id"],
        "name": row["name"],
        "image_url": row["image_url"] or "",
        "cost_points": row["cost_points"],
        "category": row["category"] or "",
        "type": row["type"],
        "period": (row["period"] if "period" in keys else "") or "",
        "subs_only": bool(row["subs_only"]),
        "vip_only": bool(row["vip_only"]),
        "description": row["description"] or "",
        "rarity": (row["rarity"] if "rarity" in keys else None) or "",
        "ends_at": (row["ends_at"] if "ends_at" in keys else None),
        "hot": bool(row["hot"]) if "hot" in keys else False,
        "stock": stock,
        "unlimited": stock == UNLIMITED_STOCK,
        "in_stock": stock != 0,
        "tickets_sold": sold,
        "max_per_person_pct": (row["max_per_person_pct"] if "max_per_person_pct" in keys else 0) or 0,
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def _flag(user, key: str) -> bool:
    """Bezpečné čtení odznaku (is_sub/is_vip) z user row."""
    try:
        return bool(user[key])
    except (KeyError, IndexError, TypeError):
        return False


def role_allows(user: Optional[sqlite3.Row], product: sqlite3.Row) -> bool:
    """Smí uživatel koupit sub-only / vip-only odměnu? Přístup řeší ODZNAK is_sub/is_vip
    NEBO role 'sub'/'vip' – takže i broadcaster/mod s odznakem SUB může kupovat sub-only tomboly."""
    if user and user["role"] == ROLE_ADMIN:
        return True
    if product["subs_only"] and not (_flag(user, "is_sub") or (user and user["role"] == "sub")):
        return False
    if product["vip_only"] and not (_flag(user, "is_vip") or (user and user["role"] == "vip")):
        return False
    return True


def _aggregate_items(items: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Sloučí stejný product_id (duplicitní řádek v košíku) do jednoho (pid, suma_qty); zachová pořadí.
    Bez toho duplicitní položka obejde kontrolu skladu i limit tomboly – každý řádek se totiž validuje
    proti stejnému (neaktualizovanému) skladu zvlášť, takže `2× sklad-1` projde a sklad spadne na -1."""
    order: List[int] = []
    agg: dict = {}
    for pid, qty in items:
        if pid not in agg:
            order.append(pid)
        agg[pid] = agg.get(pid, 0) + qty
    return [(pid, agg[pid]) for pid in order]


def validate_items(conn: sqlite3.Connection, user: sqlite3.Row,
                   items: List[Tuple[int, int]]) -> Tuple[int, Optional[str]]:
    """
    Ověří seznam (product_id, qty). Vrátí (celková_cena, chyba|None).
    Nekontroluje zůstatek bodů – ten řeší volající podle součtu.
    """
    total = 0
    disc = shop_discount_pct(conn)
    for product_id, qty in _aggregate_items(items):
        if qty < 1:
            return 0, "Neplatné množství."
        p = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not p:
            return 0, "Odměna neexistuje."
        if not p["active"]:
            return 0, f"Odměna „{p['name']}“ není aktivní."
        if not role_allows(user, p):
            label = "VIP" if p["vip_only"] else "suby"
            return 0, f"Odměna „{p['name']}“ je jen pro {label}."
        if _is_expired(p["ends_at"] if "ends_at" in p.keys() else None):
            return 0, f"Odměna „{p['name']}“ už není k dispozici (vypršela platnost)."
        if p["stock"] == 0:
            return 0, f"Odměna „{p['name']}“ není skladem."
        if p["stock"] != UNLIMITED_STOCK and qty > p["stock"]:
            return 0, f"Tolik kusů odměny „{p['name']}“ není skladem (zbývá {p['stock']})."
        # Tombola: limit ticketů na osobu (PŘÍMÝ POČET, 0 = neomezeno).
        # (Sloupec se historicky jmenuje max_per_person_pct, ale teď je to absolutní počet.)
        cap = (p["max_per_person_pct"] if "max_per_person_pct" in p.keys() else 0) or 0
        if p["type"] == "raffle" and user and cap > 0:
            mine = conn.execute(
                "SELECT COUNT(*) AS c FROM raffle_entries WHERE product_id = ? AND user_id = ?",
                (product_id, user["id"]),
            ).fetchone()["c"]
            if mine + qty > cap:
                return 0, (f"Do této tomboly si můžeš koupit nejvýše {cap} ticketů "
                           f"– zatím jich máš {mine}.")
        # CS skiny (nože/zbraně/rukavice) – bez vyplněného Steam trade linku nelze koupit
        # (jinak by objednávka nešla vyřídit; navíc tření pro odhazovací/bot účty).
        if needs_trade_link(p["category"]) and not _has_trade_link(user):
            return 0, (f"Na „{p['name']}“ potřebuješ vyplněný Steam trade link – doplň si ho "
                       f"v profilu (👤 Můj profil → 🎁 Steam trade link) a zkus to znovu.")
        total += disc_unit(p["cost_points"], disc) * qty
    return total, None


def apply_purchase(conn: sqlite3.Connection, user: sqlite3.Row,
                   items: List[Tuple[int, int]]) -> List[int]:
    """
    Provede nákup: odečte body, vytvoří objednávky (1 řádek na kus),
    sníží sklad a u typu 'raffle' přidá tikety do tomboly.
    Předpokládá, že validate_items prošlo a uživatel má dost bodů.
    Vrací seznam ID vytvořených objednávek.
    """
    order_ids: List[int] = []
    ts = now_iso()
    total = 0
    disc = shop_discount_pct(conn)
    for product_id, qty in _aggregate_items(items):
        p = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        unit = disc_unit(p["cost_points"], disc)
        # Atomický odpočet skladu: sníží JEN když je pořád dost kusů (stock >= qty). Při souběhu dvou
        # nákupů posledního kusu uspěje právě jeden – druhý má rowcount==0 → rollback (žádný přeprodej
        # do mínusu). Validace skladu výš je jen rychlá zpětná vazba; tohle je skutečná pojistka.
        if p["stock"] != UNLIMITED_STOCK:
            if conn.execute("UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                            (qty, product_id, qty)).rowcount == 0:
                conn.rollback()
                raise HTTPException(status_code=400,
                                    detail=f"Odměna „{p['name']}“ se mezitím vyprodala. Zkus to prosím znovu.")
        for _ in range(qty):
            cur = conn.execute(
                "INSERT INTO orders (user_id, product_id, product_name, points_spent, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], product_id, p["name"], unit, ORDER_PENDING, ts),
            )
            order_ids.append(cur.lastrowid)
            if p["type"] == "raffle":
                conn.execute(
                    "INSERT INTO raffle_entries (product_id, user_id, created_at) VALUES (?, ?, ?)",
                    (product_id, user["id"], ts),
                )
        total += unit * qty
    # atomický odečet – když zůstatek mezitím nestačí (souběh), vrať vše zpět
    if not try_debit(conn, user["id"], total, f"Nákup odměn ({len(order_ids)} ks)"):
        conn.rollback()
        raise HTTPException(status_code=400, detail="Nemáš dost bodů (zůstatek se mezitím změnil).")
    conn.commit()
    return order_ids
