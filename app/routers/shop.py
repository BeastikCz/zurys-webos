"""Shop: výpis odměn, detail, nákup, feed posledních nákupů, tombola, směnárna."""
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import db_dep, get_current_user, require_user
from ..db import now_iso
from ..models import PurchaseIn
from ..services import product_public, validate_items, apply_purchase, shop_discount_pct, disc_unit

router = APIRouter(prefix="/shop", tags=["shop"])

EXCHANGE_CATEGORY = "Směnárna"
# prodané tikety pro tomboly (progress bar)
_TICKETS = "(SELECT COUNT(*) FROM raffle_entries e WHERE e.product_id = p.id) AS tickets_sold"


def _apply_disc(pub: dict, disc: int) -> dict:
    """Promítne happy-hour slevu do veřejného dictu produktu (cena po slevě + původní cena)."""
    if disc > 0 and pub.get("cost_points"):
        pub["cost_orig"] = pub["cost_points"]
        pub["cost_points"] = disc_unit(pub["cost_points"], disc)
        pub["discount_pct"] = disc
    return pub


@router.get("/products")
def list_products(
    type: str = Query("all"),
    subs_only: bool = Query(False),
    vip_only: bool = Query(False),
    ending: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(8, ge=1, le=48),
    conn: sqlite3.Connection = Depends(db_dep),
):
    """Veřejný výpis aktivních odměn s filtry a stránkováním."""
    where = ["p.active = 1"]
    params: list = []
    if type and type != "all":
        where.append("p.type = ?")
        params.append(type)
    if subs_only:
        where.append("p.subs_only = 1")
    if vip_only:
        where.append("p.vip_only = 1")
    if ending:
        where.append("p.ends_at IS NOT NULL AND p.ends_at > ?")
        params.append(now_iso())
    where_sql = " AND ".join(where)
    if ending:
        order, order_params = "p.ends_at ASC", []
    else:
        # Default: nejdřív odměny co brzy KONČÍ (dle data konce, nejdřív končící první),
        # pak položky bez limitu (hot/skladem/id), expirované úplně naposled.
        order = ("CASE WHEN p.ends_at IS NULL THEN 1 WHEN p.ends_at > ? THEN 0 ELSE 2 END, "
                 "p.ends_at ASC, p.hot DESC, (p.stock = 0) ASC, p.id ASC")
        order_params = [now_iso()]

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM products p WHERE {where_sql}", params
    ).fetchone()["c"]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT p.*, {_TICKETS} FROM products p WHERE {where_sql} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        params + order_params + [page_size, offset],
    ).fetchall()

    disc = shop_discount_pct(conn)
    items = [_apply_disc(product_public(r), disc) for r in rows]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": offset + len(items) < total,
        "discount_pct": disc,
    }


@router.get("/products/{product_id}")
def product_detail(product_id: int, conn: sqlite3.Connection = Depends(db_dep)):
    row = conn.execute(
        f"SELECT p.*, {_TICKETS} FROM products p WHERE p.id = ? AND p.active = 1", (product_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Odměna nenalezena.")
    return _apply_disc(product_public(row), shop_discount_pct(conn))


@router.post("/purchase")
def purchase(data: PurchaseIn, user: sqlite3.Row = Depends(require_user),
             conn: sqlite3.Connection = Depends(db_dep)):
    """Nákup jedné odměny za body."""
    total, err = validate_items(conn, user, [(data.product_id, 1)])
    if err:
        raise HTTPException(status_code=400, detail=err)
    if user["points"] < total:
        raise HTTPException(
            status_code=400,
            detail=f"Nemáš dost bodů. Potřebuješ {total} b, máš {user['points']} b.",
        )
    order_ids = apply_purchase(conn, user, [(data.product_id, 1)])
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    product = conn.execute(
        "SELECT * FROM products WHERE id = ?", (data.product_id,)
    ).fetchone()
    return {
        "ok": True,
        "order_ids": order_ids,
        "balance": fresh["points"],
        "product": product_public(product),
        "message": f"Koupeno: {product['name']} za {total} b. Objednávka čeká na vyřízení.",
    }


@router.get("/recent")
def recent_purchases(limit: int = Query(12, ge=1, le=50),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Feed posledních nákupů napříč všemi uživateli."""
    rows = conn.execute(
        "SELECT o.id, o.points_spent, o.created_at, u.username, u.avatar_url, "
        "       COALESCE(p.name, o.product_name) AS product_name, p.type AS product_type "
        "FROM orders o JOIN users u ON u.id = o.user_id "
        "LEFT JOIN products p ON p.id = o.product_id "
        "ORDER BY o.created_at DESC, o.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "username": r["username"],
            "avatar_url": r["avatar_url"],
            "product_name": r["product_name"] or "(smazaná odměna)",
            "product_type": r["product_type"],
            "points_spent": r["points_spent"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("/milestone")
def my_milestone(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    """Kolik user celkem utratil v shopu + další milník (progress lišta „Mecenáš")."""
    spent = conn.execute("SELECT COALESCE(SUM(points_spent),0) c FROM orders WHERE user_id = ?",
                         (user["id"],)).fetchone()["c"]
    tiers = [(25000, "Sedlák mecenáš 🥉"), (100000, "Velký mecenáš 🥈"), (250000, "Magnát 🥇")]
    nxt = next((t for t in tiers if spent < t[0]), None)
    prev = 0
    for amt, _lbl in tiers:
        if spent >= amt:
            prev = amt
    return {
        "spent": spent,
        "prev_at": prev,
        "next_at": nxt[0] if nxt else None,
        "next_reward": nxt[1] if nxt else None,
        "top": nxt is None,
    }


@router.get("/activity")
def activity(limit: int = Query(20, ge=1, le=60),
             conn: sqlite3.Connection = Depends(db_dep)):
    """Živý feed aktivity pro ticker (nákupy, tikety, výhry, velké zisky)."""
    ev = []
    for r in conn.execute(
        "SELECT u.username, p.name, p.type, o.created_at FROM orders o "
        "JOIN users u ON u.id = o.user_id LEFT JOIN products p ON p.id = o.product_id "
        "ORDER BY o.created_at DESC, o.id DESC LIMIT ?", (limit,)):
        if r["type"] == "raffle":
            ev.append((r["created_at"], r["username"], f"koupil tiket {r['name'] or 'odměna'}", "ticket"))
        else:
            ev.append((r["created_at"], r["username"], f"koupil {r['name'] or 'odměnu'}", "buy"))
    for r in conn.execute(
        "SELECT u.username, p.name, w.created_at FROM raffle_winners w "
        "JOIN users u ON u.id = w.user_id LEFT JOIN products p ON p.id = w.product_id "
        "ORDER BY w.id DESC LIMIT ?", (limit,)):
        ev.append((r["created_at"], r["username"], f"vyhrál {r['name'] or 'tombolu'} 🏆", "win"))
    for r in conn.execute(
        "SELECT u.username, l.change, l.created_at FROM points_log l "
        "JOIN users u ON u.id = l.user_id WHERE l.change >= 1000 "
        "ORDER BY l.created_at DESC, l.id DESC LIMIT ?", (limit,)):
        ev.append((r["created_at"], r["username"], f"získal {r['change']} sedláků", "points"))
    ev.sort(key=lambda e: e[0], reverse=True)
    return [{"username": u, "text": t, "kind": k} for (_, u, t, k) in ev[:limit]]


@router.get("/raffle/{product_id}/entries")
def raffle_entries(product_id: int, conn: sqlite3.Connection = Depends(db_dep)):
    """Kdo nakoupil tikety do dané tomboly (+ případný výherce)."""
    rows = conn.execute(
        "SELECT u.username, u.avatar_url, COUNT(*) AS tickets "
        "FROM raffle_entries e JOIN users u ON u.id = e.user_id "
        "WHERE e.product_id = ? GROUP BY e.user_id "
        "ORDER BY tickets DESC, u.username ASC",
        (product_id,),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM raffle_entries WHERE product_id = ?", (product_id,)
    ).fetchone()["c"]
    winner = conn.execute(
        "SELECT u.username, u.avatar_url, w.created_at FROM raffle_winners w "
        "JOIN users u ON u.id = w.user_id WHERE w.product_id = ? "
        "ORDER BY w.id DESC LIMIT 1",
        (product_id,),
    ).fetchone()
    return {
        "participants": [
            {"username": r["username"], "avatar_url": r["avatar_url"], "tickets": r["tickets"]}
            for r in rows
        ],
        "total_tickets": total,
        "winner": (
            {"username": winner["username"], "avatar_url": winner["avatar_url"],
             "created_at": winner["created_at"]} if winner else None
        ),
    }


@router.get("/exchange")
def exchange_items(conn: sqlite3.Connection = Depends(db_dep)):
    """Položky směnárny (kategorie 'Směnárna')."""
    rows = conn.execute(
        "SELECT * FROM products WHERE active = 1 AND category = ? ORDER BY cost_points ASC",
        (EXCHANGE_CATEGORY,),
    ).fetchall()
    return [product_public(r) for r in rows]
