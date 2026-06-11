"""Soukromý multiplayer blackjack stůl + chat – tenký router nad enginem app.bj_room."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..deps import db_dep, require_user, require_admin, require_can_gamble
from ..models import RoomJoinIn, RoomBetIn, RoomChatIn
from .. import bj_room

router = APIRouter(prefix="/blackjack", tags=["blackjack"])


def _do(fn, conn, uid, *a):
    try:
        return fn(conn, uid, *a)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- soukromý sdílený stůl (multiplayer + chat) ----------------
@router.get("/room/mine")
def room_mine(user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    return bj_room.my_room(conn, user["id"])


@router.post("/room/create")
def room_create(user: sqlite3.Row = Depends(require_admin),
                conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.create, conn, user["id"], user["username"])


@router.post("/room/join")
def room_join(data: RoomJoinIn, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.join, conn, user["id"], user["username"], data.code)


@router.get("/room/{room_id}/state")
def room_state(room_id: int, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.state, conn, user["id"], room_id)


@router.post("/room/{room_id}/bet")
def room_bet(room_id: int, data: RoomBetIn, user: sqlite3.Row = Depends(require_user),
             conn: sqlite3.Connection = Depends(db_dep)):
    require_can_gamble(user)                # sebevyloučení ze sázek (Tipsport-style)
    return _do(bj_room.place_bet, conn, user["id"], room_id, data.amount)


@router.post("/room/{room_id}/deal")
def room_deal(room_id: int, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.deal, conn, user["id"], room_id)


@router.post("/room/{room_id}/hit")
def room_hit(room_id: int, user: sqlite3.Row = Depends(require_user),
             conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.hit, conn, user["id"], room_id)


@router.post("/room/{room_id}/stand")
def room_stand(room_id: int, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.stand, conn, user["id"], room_id)


@router.post("/room/{room_id}/next")
def room_next(room_id: int, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.next_round, conn, user["id"], room_id)


@router.post("/room/{room_id}/leave")
def room_leave(room_id: int, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.leave, conn, user["id"], room_id)


@router.post("/room/{room_id}/chat")
def room_chat(room_id: int, data: RoomChatIn, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    return _do(bj_room.chat_send, conn, user["id"], user["username"], room_id, data.msg)
