"""Mines (single-player, provably-fair): mřížka 5×5, hráč volí počet bomb (1–24), odkrývá
pole, cashne kdykoliv. 1 aktivní hra na uživatele. Pozice bomb se hráči NEposílají, dokud
hra běží (až po konci). Násobič = (1 - house edge) × inverze pravděpodobnosti přežití.
Strop výplaty MAX_PAYOUT (ochrana ekonomiky). Single-player = jen vlastní zápisy, žádná
live contention. Vypínač her řeší router dependency v main.py.
"""
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..deps import db_dep, require_user, require_can_gamble, add_points, try_debit, check_wager_limit
from ..db import now_iso
from ..models import MinesStartIn, MinesRevealIn
from ..ratelimit import rate_limit
from .. import fairness

router = APIRouter(prefix="/mines", tags=["mines"])

GRID = 25            # 5×5
MIN_MINES = 4        # minimálně 4 bomby (nerf: bylo 3)
MAX_BET = 1000       # menší výkyvy (bylo 5000)
MAX_PAYOUT = 10000   # nerf: strop výhry na hru (bylo 15000 / původně 100000)
HOUSE_EDGE = 0.12    # nerf: 12 % house edge – house vyhrává ještě víc (bylo 8 % / původně 1 %)


def _mult(revealed_count: int, mines: int) -> float:
    """Násobič po odkrytí `revealed_count` bezpečných polí: (1-edge) × inverze pravděpodobnosti."""
    m = 1.0
    for i in range(revealed_count):
        m *= (GRID - i) / (GRID - mines - i)
    return (1.0 - HOUSE_EDGE) * m


def _next_mult(revealed_count: int, mines: int) -> float:
    """Násobič, KDYBY hráč odkryl ještě jedno bezpečné pole (pro UI 'další pole = ×')."""
    safe = GRID - mines
    return _mult(min(revealed_count + 1, safe), mines)


def _payout(bet: int, revealed_count: int, mines: int) -> int:
    return min(MAX_PAYOUT, int(bet * _mult(revealed_count, mines)))


def _fair_consume(conn, uid: int):
    """Zajistí provably-fair seedy (lazy init) a vrátí (ss, sh, cs, nonce) pro tuto hru.
    Nonce bumpne volající AŽ po použití (stejně jako kolo)."""
    row = conn.execute(
        "SELECT fair_server_seed, fair_server_hash, fair_client_seed, fair_nonce FROM users WHERE id=?",
        (uid,)).fetchone()
    if not row["fair_server_seed"]:
        ss = fairness.new_server_seed()
        conn.execute(
            "UPDATE users SET fair_server_seed=?, fair_server_hash=?, fair_client_seed=?, fair_nonce=0 "
            "WHERE id=? AND fair_server_seed IS NULL",
            (ss, fairness.seed_hash(ss), fairness.new_client_seed(), uid))
        row = conn.execute(
            "SELECT fair_server_seed, fair_server_hash, fair_client_seed, fair_nonce FROM users WHERE id=?",
            (uid,)).fetchone()
    return row["fair_server_seed"], row["fair_server_hash"], row["fair_client_seed"], row["fair_nonce"] or 0


def _state(conn, g, reveal_layout: bool) -> dict:
    """Stav hry pro klienta. Bomby (`layout`) jen když hra skončila (nebo reveal_layout)."""
    revealed = json.loads(g["revealed"] or "[]")
    out = {
        "id": g["id"], "bet": g["bet"], "mines": g["mines"], "status": g["status"],
        "revealed": revealed, "safe_count": len(revealed),
        "mult": round(_mult(len(revealed), g["mines"]), 4),
        "next_mult": round(_next_mult(len(revealed), g["mines"]), 4),
        "cashout": _payout(g["bet"], len(revealed), g["mines"]),
        "payout": g["payout"],
        "fair": {"server_hash": g["server_hash"], "client_seed": g["client_seed"], "nonce": g["nonce"]},
    }
    if reveal_layout or g["status"] != "active":
        out["layout"] = json.loads(g["layout"] or "[]")
    return out


def _active(conn, uid):
    return conn.execute(
        "SELECT * FROM mines_games WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
        (uid,)).fetchone()


@router.get("/state")
def state(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    g = _active(conn, user["id"])
    fresh = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"active": bool(g), "game": _state(conn, g, False) if g else None,
            "max_bet": MAX_BET, "grid": GRID, "balance": fresh["points"]}


@router.post("/start")
def start(data: MinesStartIn, user: sqlite3.Row = Depends(require_user),
          conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"mines:start:{user['id']}", 120, 60)   # strop jen proti botům (~2 hry/s); pro hráče neviditelné
    require_can_gamble(user)                 # sebevyloučení ze sázek
    if _active(conn, user["id"]):
        raise HTTPException(status_code=400, detail="Máš rozehranou hru – dohraj ji nebo cashni.")
    bet, mines = data.bet, data.mines
    if bet < 1 or bet > MAX_BET:
        raise HTTPException(status_code=400, detail=f"Sázka musí být 1–{MAX_BET} sedláků.")
    if mines < MIN_MINES or mines > GRID - 1:
        raise HTTPException(status_code=400, detail=f"Počet bomb musí být {MIN_MINES}–24.")
    check_wager_limit(conn, user, bet)               # responsible gaming: denní limit sázek
    if not try_debit(conn, user["id"], bet, f"Mines sázka ({mines} bomb)"):
        raise HTTPException(status_code=400, detail=f"Nemáš dost sedláků (sázka {bet}).")
    ss, sh, cs, nonce = _fair_consume(conn, user["id"])
    layout = fairness.mine_positions(ss, cs, nonce, GRID, mines)
    conn.execute("UPDATE users SET fair_nonce = fair_nonce + 1 WHERE id=?", (user["id"],))
    cur = conn.execute(
        "INSERT INTO mines_games (user_id,bet,mines,layout,revealed,status,server_hash,client_seed,nonce,created_at) "
        "VALUES (?,?,?,?,'[]','active',?,?,?,?)",
        (user["id"], bet, mines, json.dumps(layout), sh, cs, nonce, now_iso()))
    conn.commit()
    g = conn.execute("SELECT * FROM mines_games WHERE id=?", (cur.lastrowid,)).fetchone()
    fresh = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"game": _state(conn, g, False), "balance": fresh["points"]}


@router.post("/reveal")
def reveal(data: MinesRevealIn, user: sqlite3.Row = Depends(require_user),
           conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"mines:reveal:{user['id']}", 600, 30)   # strop jen proti botům (~20 odkrytí/s); pro hráče neviditelné
    g = _active(conn, user["id"])
    if not g:
        raise HTTPException(status_code=400, detail="Nemáš rozehranou hru.")
    tile = data.tile
    if tile < 0 or tile >= GRID:
        raise HTTPException(status_code=400, detail="Neplatné pole.")
    revealed = json.loads(g["revealed"] or "[]")
    if tile in revealed:
        raise HTTPException(status_code=400, detail="Pole už je odkryté.")
    layout = json.loads(g["layout"] or "[]")
    if tile in layout:
        conn.execute("UPDATE mines_games SET status='busted', ended_at=? WHERE id=?", (now_iso(), g["id"]))
        conn.commit()
        g = conn.execute("SELECT * FROM mines_games WHERE id=?", (g["id"],)).fetchone()
        st = _state(conn, g, True)
        st["hit"] = tile
        fresh = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()
        return {"game": st, "balance": fresh["points"], "busted": True}
    revealed.append(tile)
    safe = GRID - g["mines"]
    if len(revealed) >= safe:                # full clear → auto cashout
        payout = _payout(g["bet"], len(revealed), g["mines"])
        add_points(conn, user["id"], payout, f"Mines výhra – full clear ({g['mines']} bomb)")
        conn.execute("UPDATE mines_games SET revealed=?, status='cashed', payout=?, ended_at=? WHERE id=?",
                     (json.dumps(revealed), payout, now_iso(), g["id"]))
        conn.commit()
        g = conn.execute("SELECT * FROM mines_games WHERE id=?", (g["id"],)).fetchone()
        fresh = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()
        return {"game": _state(conn, g, True), "balance": fresh["points"], "cashed": True, "payout": payout}
    conn.execute("UPDATE mines_games SET revealed=? WHERE id=?", (json.dumps(revealed), g["id"]))
    conn.commit()
    g = conn.execute("SELECT * FROM mines_games WHERE id=?", (g["id"],)).fetchone()
    return {"game": _state(conn, g, False)}


@router.post("/cashout")
def cashout(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    g = _active(conn, user["id"])
    if not g:
        raise HTTPException(status_code=400, detail="Nemáš rozehranou hru.")
    revealed = json.loads(g["revealed"] or "[]")
    if not revealed:
        raise HTTPException(status_code=400, detail="Odkryj aspoň jedno pole, než cashneš.")
    mult = _mult(len(revealed), g["mines"])
    payout = _payout(g["bet"], len(revealed), g["mines"])
    add_points(conn, user["id"], payout, f"Mines cashout (×{round(mult, 2)})")
    conn.execute("UPDATE mines_games SET status='cashed', payout=?, ended_at=? WHERE id=?",
                 (payout, now_iso(), g["id"]))
    conn.commit()
    g = conn.execute("SELECT * FROM mines_games WHERE id=?", (g["id"],)).fetchone()
    fresh = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"game": _state(conn, g, True), "balance": fresh["points"], "cashed": True, "payout": payout}
