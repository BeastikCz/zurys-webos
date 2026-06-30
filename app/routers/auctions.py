"""Aukce o skiny – veřejné endpointy (seznam + příhoz). Admin (vystavit/zrušit) je v admin.py."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..deps import db_dep, require_user
from ..models import AuctionBidIn
from ..ratelimit import rate_limit
from .. import auctions

router = APIRouter(prefix="/auctions", tags=["auctions"])


@router.get("")
def list_auctions(conn: sqlite3.Connection = Depends(db_dep)):
    """Aktivní + nedávno skončené aukce (lazy finalizace skončených). Veřejné (i bez přihlášení)."""
    return auctions.list_public(conn)


@router.post("/{auction_id}/bid")
def bid_auction(auction_id: int, data: AuctionBidIn, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Přihoď sedláky na aukci (escrow – sedláci se zablokují, přehození je vrátí)."""
    rate_limit(f"auctionbid:{user['id']}", 8, 20)        # anti-spam: max 8 příhozů / 20 s
    r = auctions.bid(conn, user, auction_id, data.amount)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Příhoz se teď nepodařil."))
    return r
