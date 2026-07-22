"""Aukce o skiny – veřejné endpointy (seznam, příhoz a návrhy komunitního Trhu)."""
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..db import now_iso
from ..deps import db_dep, notify, require_user
from ..models import AuctionBidIn, DmIn, MarketSubmissionIn, SkinSearchIn
from ..ratelimit import rate_limit
from .. import auctions, cs_skins

from ..microcache import ttl_cache

router = APIRouter(prefix="/auctions", tags=["auctions"])


@router.get("")
@ttl_cache(2)
def list_auctions(conn: sqlite3.Connection = Depends(db_dep)):
    """Aktivní + nedávno skončené aukce (lazy finalizace skončených). Veřejné (i bez přihlášení)."""
    return auctions.list_public(conn)


@router.get("/my-sales")
def my_market_sales(user: sqlite3.Row = Depends(require_user),
                    conn: sqlite3.Connection = Depends(db_dep)):
    """Soukromé kontakty na výherce komunitních nabídek přístupné jen jejich prodávajícímu."""
    rows = conn.execute(
        "SELECT a.id,a.title,a.image_url,a.current_bid AS final_price,a.sale_type,a.seller_paid_at,"
        "a.wear,a.float_value,a.delivery_status,a.sold_at,a.delivery_sent_at,a.delivery_completed_at,a.dispute_reason, "
        "w.username AS winner,w.kick_username AS winner_kick,w.steam_trade_url AS winner_trade_url, "
        "(SELECT COUNT(*) FROM auctions done WHERE done.winner_id=w.id AND done.delivery_status='completed') "
        "AS winner_completed_purchases "
        "FROM auctions a JOIN users w ON w.id=a.winner_id "
        "WHERE a.seller_user_id=? AND a.status='ended' AND a.winner_id IS NOT NULL "
        "ORDER BY a.id DESC LIMIT 20", (user["id"],)).fetchall()
    purchases = conn.execute(
        "SELECT a.id,a.title,a.image_url,a.current_bid AS final_price,a.sale_type,a.seller_paid_at,"
        "a.wear,a.float_value,a.delivery_status,a.sold_at,a.delivery_sent_at,a.delivery_completed_at,a.dispute_reason, "
        "s.username AS seller,s.kick_username AS seller_kick, "
        "(SELECT COUNT(*) FROM auctions done WHERE done.seller_user_id=s.id AND done.delivery_status='completed') "
        "AS seller_completed_sales "
        "FROM auctions a JOIN users s ON s.id=a.seller_user_id "
        "WHERE a.winner_id=? AND a.status='ended' AND a.seller_user_id IS NOT NULL "
        "ORDER BY a.id DESC LIMIT 20", (user["id"],)).fetchall()
    return {"sales": [dict(r) for r in rows], "purchases": [dict(r) for r in purchases]}


def _market_chat_deal(conn, auction_id: int):
    deal = conn.execute(
        "SELECT a.id,a.title,a.image_url,a.current_bid AS final_price,a.sale_type,a.delivery_status,a.sold_at,"
        "a.seller_user_id,a.winner_id,s.username AS seller,s.kick_username AS seller_kick,"
        "w.username AS winner,w.kick_username AS winner_kick,"
        "(SELECT COUNT(*) FROM auctions done WHERE done.seller_user_id=s.id AND done.delivery_status='completed') "
        "AS seller_completed_sales,"
        "(SELECT COUNT(*) FROM auctions done WHERE done.winner_id=w.id AND done.delivery_status='completed') "
        "AS winner_completed_purchases "
        "FROM auctions a JOIN users s ON s.id=a.seller_user_id JOIN users w ON w.id=a.winner_id "
        "WHERE a.id=? AND a.status='ended' AND a.seller_user_id IS NOT NULL AND a.winner_id IS NOT NULL",
        (auction_id,)).fetchone()
    if not deal:
        raise HTTPException(status_code=404, detail="Dokončený komunitní obchod neexistuje.")
    return deal


def _market_chat_access(deal, user) -> bool:
    participant = user["id"] in (deal["seller_user_id"], deal["winner_id"])
    staff = user["role"] in ("admin", "broadcaster")
    if not participant and not staff:
        raise HTTPException(status_code=403, detail="Tento chat patří pouze prodávajícímu a výherci.")
    return participant or (staff and deal["delivery_status"] == "disputed")


@router.get("/{auction_id}/chat")
def market_chat(auction_id: int, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    deal = _market_chat_deal(conn, auction_id)
    can_send = _market_chat_access(deal, user)
    if can_send:
        conn.execute("UPDATE market_messages SET seen=1 WHERE auction_id=? AND from_id<>? AND seen=0",
                     (auction_id, user["id"]))
        conn.commit()
    rows = conn.execute(
        "SELECT m.id,m.from_id,m.body,m.created_at,u.username AS from_name,u.role AS from_role "
        "FROM market_messages m JOIN users u ON u.id=m.from_id WHERE m.auction_id=? ORDER BY m.id",
        (auction_id,)).fetchall()
    return {
        "auction": {"id": deal["id"], "title": deal["title"], "image_url": deal["image_url"] or "",
                    "final_price": deal["final_price"], "sale_type": deal["sale_type"],
                    "delivery_status": deal["delivery_status"], "sold_at": deal["sold_at"]},
        "seller": {"username": deal["seller"], "kick_username": deal["seller_kick"],
                   "completed_trades": deal["seller_completed_sales"]},
        "winner": {"username": deal["winner"], "kick_username": deal["winner_kick"],
                   "completed_trades": deal["winner_completed_purchases"]},
        "can_send": can_send,
        "messages": [{"id": r["id"], "body": r["body"], "created_at": r["created_at"],
                      "from_name": r["from_name"], "from_role": r["from_role"],
                      "mine": r["from_id"] == user["id"]} for r in rows],
    }


@router.post("/{auction_id}/chat")
def market_chat_send(auction_id: int, data: DmIn, user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    deal = _market_chat_deal(conn, auction_id)
    if not _market_chat_access(deal, user):
        raise HTTPException(status_code=403, detail="Admin může psát až po otevření sporu.")
    body = (data.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Prázdná zpráva.")
    rate_limit(f"market:chat:{user['id']}", 10, 60)
    conn.execute("INSERT INTO market_messages (auction_id,from_id,body,created_at) VALUES (?,?,?,?)",
                 (auction_id, user["id"], body[:2000], now_iso()))
    recipients = ((deal["winner_id"],) if user["id"] == deal["seller_user_id"] else
                  (deal["seller_user_id"],) if user["id"] == deal["winner_id"] else
                  (deal["seller_user_id"], deal["winner_id"]))
    for recipient in recipients:
        notify(conn, recipient, "💬", f"Nová zpráva k obchodu #{auction_id}",
               f"{user['username']}: {body[:180]}", "#/shop")
    conn.commit()
    return {"ok": True}


@router.post("/{auction_id}/delivery/sent")
def market_delivery_sent(auction_id: int, user: sqlite3.Row = Depends(require_user),
                         conn: sqlite3.Connection = Depends(db_dep)):
    r = auctions.mark_delivered(conn, user, auction_id)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Odeslání nejde potvrdit."))
    return r


@router.post("/{auction_id}/delivery/confirm")
def market_delivery_confirm(auction_id: int, user: sqlite3.Row = Depends(require_user),
                            conn: sqlite3.Connection = Depends(db_dep)):
    r = auctions.confirm_delivery(conn, user, auction_id)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Převzetí nejde potvrdit."))
    return r


@router.post("/{auction_id}/delivery/dispute")
def market_delivery_dispute(auction_id: int, data: DmIn,
                            user: sqlite3.Row = Depends(require_user),
                            conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"market:dispute:{user['id']}", 3, 3600)
    r = auctions.dispute_delivery(conn, user, auction_id, data.body)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Spor nejde otevřít."))
    return r


@router.post("/submissions")
def submit_market_skin(data: MarketSubmissionIn, user: sqlite3.Row = Depends(require_user),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Uloží neveřejný návrh skinu. Na Trh se dostane až po schválení týmem."""
    rate_limit(f"market:submit:{user['id']}", 5, 3600)
    if conn.execute("SELECT COUNT(*) c FROM market_submissions WHERE user_id=? AND status='pending'",
                    (user["id"],)).fetchone()["c"] >= 5:
        raise HTTPException(status_code=400, detail="Nejdřív počkej na vyřízení svých čekajících nabídek.")
    inspect_url = (data.inspect_url or "").strip()
    if inspect_url and not re.match(r"^(https://steamcommunity\.com/|steam://rungame/730/)", inspect_url, re.I):
        raise HTTPException(status_code=400, detail="Inspect link musí vést na Steam.")
    wear = auctions.wear_from_float(data.float_value) if data.float_value is not None else data.wear
    cur = conn.execute(
        "INSERT INTO market_submissions (user_id,title,image_url,description,wear,float_value,inspect_url,price,sale_type,duration_minutes,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)",
        (user["id"], data.title.strip(), auctions._safe_image_url(data.image_url),
         data.description.strip(), wear, data.float_value, inspect_url, data.price, data.sale_type,
         data.duration_minutes, now_iso()))
    conn.commit()
    return {"ok": True, "id": cur.lastrowid, "status": "pending"}


@router.post("/skin-search")
def market_skin_search(data: SkinSearchIn, user: sqlite3.Row = Depends(require_user)):
    rate_limit(f"market:skin-search:{user['id']}", 20, 60)
    return {"results": cs_skins.search(data.query, 24), "ready": cs_skins.ready()}


@router.post("/{auction_id}/bid")
def bid_auction(auction_id: int, data: AuctionBidIn, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Přihoď sedláky na aukci (escrow – sedláci se zablokují, přehození je vrátí)."""
    rate_limit(f"auctionbid:{user['id']}", 8, 20)        # anti-spam: max 8 příhozů / 20 s
    r = auctions.bid(conn, user, auction_id, data.amount)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Příhoz se teď nepodařil."))
    return r


@router.post("/{auction_id}/buynow")
def buynow_auction(auction_id: int, user: sqlite3.Row = Depends(require_user),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Kup teď: zaplať buy_now cenu → okamžitá výhra + konec aukce."""
    rate_limit(f"auctionbuy:{user['id']}", 4, 20)
    r = auctions.buy_now(conn, user, auction_id)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Kup teď se teď nepodařil."))
    return r
