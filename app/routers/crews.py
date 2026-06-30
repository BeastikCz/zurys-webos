"""Crew / Parta (klany) – tenký router nad enginem app.crews."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..deps import db_dep, require_user, require_early_access
from ..models import (CrewCreateIn, CrewJoinIn, CrewChatIn, CrewMemberIn, CrewRoleIn,
                      CrewEmblemIn, CrewMotdIn, CrewPrivateIn)
from .. import crews

# early access gate: celý Crew router je zatím jen pro grantnuté + admina (soft launch)
router = APIRouter(prefix="/crews", tags=["crews"], dependencies=[Depends(require_early_access)])


def _do(fn, conn, uid, *a):
    try:
        return fn(conn, uid, *a)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/leaderboard")
def crews_leaderboard(sort: str = "week", user: sqlite3.Row = Depends(require_user),
                      conn: sqlite3.Connection = Depends(db_dep)):
    return crews.leaderboard(conn, user["id"], sort=sort)


@router.get("/mine")
def crews_mine(user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return crews.my_crew(conn, user["id"])


@router.get("/tags")
def crews_tags(user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    """Mapa username→TAG všech členů crew (pro [TAG] u nicku globálně)."""
    return crews.tags(conn)


@router.get("/emblems")
def crews_emblems(user: sqlite3.Row = Depends(require_user)):
    """Povolené emblémy + cena změny (pro vůdcův picker)."""
    return {"emblems": crews.EMBLEMS, "cost": crews.EMBLEM_COST}


@router.post("/create")
def crews_create(data: CrewCreateIn, user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.create, conn, user["id"], user["username"], data.name, data.tag)


@router.post("/join")
def crews_join(data: CrewJoinIn, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.join, conn, user["id"], user["username"], data.code)


@router.get("/{crew_id}")
def crews_detail(crew_id: int, user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    d = crews.state(conn, user["id"], crew_id)
    if not d:
        raise HTTPException(status_code=404, detail="Parta nenalezena.")
    return d


@router.post("/{crew_id}/leave")
def crews_leave(crew_id: int, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    return crews.leave(conn, user["id"])


@router.post("/{crew_id}/claim-goal")
def crews_claim_goal(crew_id: int, user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvednutí týdenní odměny za splněný crew cíl (1×/týden/člen)."""
    return _do(crews.claim_goal, conn, user["id"])


@router.post("/{crew_id}/chat")
def crews_chat(crew_id: int, data: CrewChatIn, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.chat_send, conn, user["id"], user["username"], crew_id, data.msg)


@router.post("/{crew_id}/kick")
def crews_kick(crew_id: int, data: CrewMemberIn, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.kick, conn, user["id"], data.user_id)


@router.post("/{crew_id}/role")
def crews_role(crew_id: int, data: CrewRoleIn, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.set_role, conn, user["id"], data.user_id, data.role)


@router.post("/{crew_id}/emblem")
def crews_emblem(crew_id: int, data: CrewEmblemIn, user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.set_emblem, conn, user["id"], data.emblem)


@router.post("/{crew_id}/motd")
def crews_motd(crew_id: int, data: CrewMotdIn, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.set_motd, conn, user["id"], data.text)


@router.post("/{crew_id}/private")
def crews_private(crew_id: int, data: CrewPrivateIn, user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.toggle_private, conn, user["id"], data.private)


@router.post("/{crew_id}/approve")
def crews_approve(crew_id: int, data: CrewMemberIn, user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.approve_request, conn, user["id"], data.user_id)


@router.post("/{crew_id}/reject")
def crews_reject(crew_id: int, data: CrewMemberIn, user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    return _do(crews.reject_request, conn, user["id"], data.user_id)
