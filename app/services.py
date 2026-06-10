"""Sdílená obchodní logika: validace a provedení nákupu (shop i košík)."""
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException

from .config import ROLE_ADMIN, ORDER_PENDING, UNLIMITED_STOCK, SKIN_TRADE_KEYWORDS
from .db import now_iso
from .deps import add_points, try_debit


def needs_trade_link(category) -> bool:
    """Vyžaduje tato kategorie odměny Steam trade link? (CS skiny – nože/rukavice/zbraně)"""
    c = (category or "").lower()
    return any(k in c for k in SKIN_TRADE_KEYWORDS)


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


def validate_items(conn: sqlite3.Connection, user: sqlite3.Row,
                   items: List[Tuple[int, int]]) -> Tuple[int, Optional[str]]:
    """
    Ověří seznam (product_id, qty). Vrátí (celková_cena, chyba|None).
    Nekontroluje zůstatek bodů – ten řeší volající podle součtu.
    """
    total = 0
    for product_id, qty in items:
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
            return 0, f"Odměny „{p['name']}“ není tolik skladem (zbývá {p['stock']})."
        # Tombola: limit ticketů na osobu (PŘÍMÝ POČET, 0 = neomezeno).
        # (Sloupec se historicky jmenuje max_per_person_pct, ale teď je to absolutní počet.)
        cap = (p["max_per_person_pct"] if "max_per_person_pct" in p.keys() else 0) or 0
        if p["type"] == "raffle" and user and cap > 0:
            mine = conn.execute(
                "SELECT COUNT(*) AS c FROM raffle_entries WHERE product_id = ? AND user_id = ?",
                (product_id, user["id"]),
            ).fetchone()["c"]
            if mine + qty > cap:
                return 0, (f"Do této tomboly si můžeš koupit max {cap} ticketů na osobu "
                           f"– máš jich {mine}.")
        # CS skiny (nože/zbraně/rukavice) – bez vyplněného Steam trade linku nelze koupit
        # (jinak by objednávka nešla vyřídit; navíc tření pro odhazovací/bot účty).
        if needs_trade_link(p["category"]) and not _has_trade_link(user):
            return 0, (f"Na „{p['name']}“ potřebuješ vyplněný Steam trade link – doplň si ho "
                       f"v profilu (👤 Můj profil → 🎁 Steam trade link) a zkus to znovu.")
        total += p["cost_points"] * qty
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
    for product_id, qty in items:
        p = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        for _ in range(qty):
            cur = conn.execute(
                "INSERT INTO orders (user_id, product_id, product_name, points_spent, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], product_id, p["name"], p["cost_points"], ORDER_PENDING, ts),
            )
            order_ids.append(cur.lastrowid)
            if p["type"] == "raffle":
                conn.execute(
                    "INSERT INTO raffle_entries (product_id, user_id, created_at) VALUES (?, ?, ?)",
                    (product_id, user["id"], ts),
                )
        if p["stock"] != UNLIMITED_STOCK:
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, product_id))
        total += p["cost_points"] * qty
    # atomický odečet – když zůstatek mezitím nestačí (souběh), vrať vše zpět
    if not try_debit(conn, user["id"], total, f"Nákup odměn ({len(order_ids)} ks)"):
        conn.rollback()
        raise HTTPException(status_code=400, detail="Nemáš dost bodů (zůstatek se mezitím změnil).")
    conn.commit()
    return order_ids
