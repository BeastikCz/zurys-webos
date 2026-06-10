"""Nákupní košík: koupě více odměn najednou."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from ..anticheat import check_or_block
from ..deps import db_dep, require_user
from ..models import CartCheckoutIn
from ..services import validate_items, apply_purchase

router = APIRouter(prefix="/cart", tags=["cart"])


@router.post("/checkout")
def checkout(data: CartCheckoutIn, request: Request,
             user: sqlite3.Row = Depends(require_user),
             conn: sqlite3.Connection = Depends(db_dep)):
    if not data.items:
        raise HTTPException(status_code=400, detail="Košík je prázdný.")
    check_or_block(conn, user, request, context="purchase", t0_ms=data.t0,
                   block_msg="Nákup zablokován ochranou proti zneužití.")
    items = [(it.product_id, it.qty) for it in data.items]
    total, err = validate_items(conn, user, items)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if user["points"] < total:
        raise HTTPException(
            status_code=400,
            detail=f"Nemáš dost bodů. Košík stojí {total} b, máš {user['points']} b.",
        )
    order_ids = apply_purchase(conn, user, items)
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True,
        "order_ids": order_ids,
        "count": len(order_ids),
        "spent": total,
        "balance": fresh["points"],
        "message": f"Hotovo! Koupeno {len(order_ids)} ks za {total} b.",
    }
