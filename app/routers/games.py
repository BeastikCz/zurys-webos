"""PvP hry o body: piškvorky (gomoku, 5 v řadě), 1v1 se sázkou.

Pravidla férovosti:
- Tahy validuje SERVER (klient nemůže podvádět – kontrola, čí je tah, volné políčko, výhra).
- Vklady obou hráčů jsou v „escrow" (odečtou se hned), vítěz bere bank (volitelně mínus rake).
- Anticheat: dva NE-admin účty ze stejné IP / zařízení proti sobě hrát nemohou (anti-farma).
- Timeout: když hráč 90 s netáhne, soupeř může nárokovat výhru (anti-zdrhnutí).
"""
import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import ROLE_ADMIN
from ..db import now_iso, get_setting
from ..deps import db_dep, require_user, add_points, try_debit, client_ip, to_public, require_can_gamble, check_wager_limit
from ..economy import note_game_net, games_capped
from ..models import GameCreateIn, GameMoveIn, DuelCreateIn
from ..ratelimit import rate_limit
from ..security import secure_choice, secure_randint

router = APIRouter(prefix="/games", tags=["games"])

BOARD = 9               # hrací plocha 9×9 (menší = rychlejší hry + lepší na mobil)
WIN = 5                 # 5 v řadě
MOVE_TIMEOUT = 12       # s – limit na JEDEN tah; po překročení prohrává ten, kdo je na tahu (auto)
FIRST_MOVE_TIMEOUT = 30 # s – PRVNÍ tah po nalezení soupeře má delší grace: zakladatel v ten moment
                        #     nemusí koukat na desku (čekal / sleduje stream v jiném tabu), ať hned
                        #     neprohraje, než si match všimne (i přes zvuk/upozornění). Pak už blitz.
GAME_MAX_S = 210        # s – celá hra max 3,5 min; po překročení prohrává ten, kdo je na tahu
MIN_MOVE_S = 0.5        # s – tah rychlejší než tohle = bot → odmítnuto (anti-script)
OPEN_TTL_S = 30 * 60    # otevřená hra bez soupeře po 30 min expiruje (vrátí vklad)

GAMES_MAINTENANCE = False  # (Vypínání her řeší GAMES_OFF v main.py na úrovni celého routeru.)


def _empty_board() -> str:
    return "." * (BOARD * BOARD)


def _check_win(board: str, last: int, sym: str) -> bool:
    """Je na pozici `last` dokončená řada 5× symbolu `sym`?"""
    r0, c0 = divmod(last, BOARD)
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        count = 1
        for sign in (1, -1):
            r, c = r0 + dr * sign, c0 + dc * sign
            while 0 <= r < BOARD and 0 <= c < BOARD and board[r * BOARD + c] == sym:
                count += 1
                r += dr * sign
                c += dc * sign
        if count >= WIN:
            return True
    return False


def _rake_pct(conn) -> int:
    try:
        return max(0, min(50, int(get_setting(conn, "games_rake_pct", "0") or "0")))
    except (ValueError, TypeError):
        return 0


def _same_person(conn, uid1: int, uid2: int) -> bool:
    """Sdílí dva účty IP nebo otisk zařízení? (anti-farma: hra sám proti sobě)"""
    def ips(uid):
        return {r["ip"] for r in conn.execute(
            "SELECT DISTINCT ip FROM login_events WHERE user_id=? AND ip IS NOT NULL AND ip!=''", (uid,))}

    def fps(uid):
        return {r["fp_hash"] for r in conn.execute(
            "SELECT DISTINCT fp_hash FROM client_signals WHERE user_id=? AND fp_hash IS NOT NULL", (uid,))}
    if ips(uid1) & ips(uid2):
        return True
    if fps(uid1) & fps(uid2):
        return True
    return False


def _seconds_since(iso_ts: str) -> float:
    if not iso_ts:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_ts)).total_seconds()
    except (ValueError, TypeError):
        return 1e9


def _move_limit(g) -> int:
    """Limit na AKTUÁLNÍ tah. První tah (move_count==0, hned po nalezení soupeře) má delší
    grace – zakladatel v ten moment nemusí koukat na desku (čekal / sleduje stream v jiném
    tabu), ať kvůli tomu hned neprohraje. Další tahy mají rychlý blitz limit MOVE_TIMEOUT."""
    return FIRST_MOVE_TIMEOUT if g["move_count"] == 0 else MOVE_TIMEOUT


def _expire_open(conn) -> None:
    """Vrátí vklad u otevřených her, na které se nikdo dlouho nepřidal."""
    rows = conn.execute("SELECT * FROM games WHERE status='open'").fetchall()
    changed = False
    for g in rows:
        if _seconds_since(g["created_at"]) > OPEN_TTL_S:
            add_points(conn, g["p1_id"], g["stake"], f"Vypršelá hra #{g['id']} – vrácení vkladu")
            conn.execute("UPDATE games SET status='cancelled', updated_at=? WHERE id=?",
                         (now_iso(), g["id"]))
            changed = True
    if changed:
        conn.commit()


def refund_all_open(conn) -> int:
    """Vrátí vklad a zruší VŠECHNY otevřené hry (volá se při zapnutí údržby, ať nikomu
    nezůstanou body v escrow, když je lobby vypnuté). Aktivní rozehrané hry nechává být."""
    rows = conn.execute("SELECT id, p1_id, stake FROM games WHERE status='open'").fetchall()
    for g in rows:
        add_points(conn, g["p1_id"], g["stake"], f"Hra #{g['id']} zrušena (údržba) – vrácení vkladu")
        conn.execute("UPDATE games SET status='cancelled', updated_at=? WHERE id=?", (now_iso(), g["id"]))
    if rows:
        conn.commit()
    return len(rows)


def refund_open_duels(conn) -> int:
    """Vrátí vklad a zruší VŠECHNY otevřené duely (coinflip/dice čekající na soupeře).
    Aktivní duely prakticky nevznikají – vyhodnotí se hned při obsazení slotu."""
    rows = conn.execute("SELECT id, p1_id, stake FROM duels WHERE status='open'").fetchall()
    for d in rows:
        add_points(conn, d["p1_id"], d["stake"], f"Duel #{d['id']} zrušen (hry mimo provoz) – vrácení vkladu")
        conn.execute("UPDATE duels SET status='cancelled', updated_at=? WHERE id=?", (now_iso(), d["id"]))
    if rows:
        conn.commit()
    return len(rows)


def cancel_inprogress_refund(conn) -> int:
    """Vrátí vklady a zruší VŠECHNY otevřené i rozehrané hry (jednorázově při změně velikosti
    desky 12×12 → 9×9, ať nezůstanou rozehrané hry ve staré velikosti). U aktivní hry vrací oběma."""
    rows = conn.execute(
        "SELECT id, p1_id, p2_id, stake FROM games WHERE status IN ('open', 'active')"
    ).fetchall()
    for g in rows:
        add_points(conn, g["p1_id"], g["stake"], f"Hra #{g['id']} zrušena (nová deska 9×9) – vrácení vkladu")
        if g["p2_id"]:
            add_points(conn, g["p2_id"], g["stake"], f"Hra #{g['id']} zrušena (nová deska 9×9) – vrácení vkladu")
        conn.execute("UPDATE games SET status='cancelled', updated_at=? WHERE id=?", (now_iso(), g["id"]))
    if rows:
        conn.commit()
    return len(rows)


def list_games_admin(conn) -> list:
    """Pro admina: seznam probíhajících (otevřených + rozehraných) her – kdo hraje, sázka, kdy."""
    rows = conn.execute(
        "SELECT g.id, g.status, g.stake, g.move_count, g.created_at, g.last_move_at, "
        "g.p1_id, g.p2_id, p1.username AS p1_name, p2.username AS p2_name "
        "FROM games g LEFT JOIN users p1 ON p1.id = g.p1_id LEFT JOIN users p2 ON p2.id = g.p2_id "
        "WHERE g.status IN ('open', 'active') ORDER BY g.id DESC LIMIT 100"
    ).fetchall()
    return [{
        "id": g["id"], "status": g["status"], "stake": g["stake"],
        "pot": g["stake"] * (2 if g["status"] == "active" else 1),
        "p1": g["p1_name"] or "?", "p2": g["p2_name"] or "—",
        "move_count": g["move_count"], "created_at": g["created_at"], "last_move_at": g["last_move_at"],
    } for g in rows]


def cancel_game_admin(conn, gid: int) -> dict:
    """Admin ukončí jednu hru: vrátí vklady (open = p1; active = oba) a nastaví cancelled."""
    g = conn.execute("SELECT id, status, p1_id, p2_id, stake FROM games WHERE id = ?", (gid,)).fetchone()
    if not g:
        return {"ok": False, "error": "Hra nenalezena."}
    if g["status"] not in ("open", "active"):
        return {"ok": False, "error": f"Hra už není rozehraná (stav: {g['status']})."}
    add_points(conn, g["p1_id"], g["stake"], f"Hra #{gid} ukončena adminem – vrácení vkladu")
    if g["status"] == "active" and g["p2_id"]:
        add_points(conn, g["p2_id"], g["stake"], f"Hra #{gid} ukončena adminem – vrácení vkladu")
    conn.execute("UPDATE games SET status = 'cancelled', updated_at = ? WHERE id = ?", (now_iso(), gid))
    conn.commit()
    return {"ok": True, "id": gid}


def _hist_row(kind, r, p1n, p2n, duel=False):
    win = p1n if r["winner"] == 1 else (p2n if r["winner"] == 2 else ("remíza" if r["winner"] == 0 else None))
    return {"kind": kind, "duel": duel, "id": r["id"], "status": r["status"], "stake": r["stake"],
            "bank": r["stake"] * 2, "p1": p1n or "?", "p2": p2n or "—", "winner": win,
            "when": r["updated_at"] or r["created_at"],
            "refundable": r["status"] == "finished" and r["winner"] in (1, 2)}


def games_history(conn, limit: int = 60) -> list:
    """Dohrané/zrušené hry (piškvorky + duely) pro admin – kdo s kým, kdo vyhrál, refundovatelnost."""
    out = []
    for g in conn.execute(
        "SELECT g.id, g.status, g.stake, g.winner, g.updated_at, g.created_at, "
        "p1.username AS p1n, p2.username AS p2n FROM games g "
        "LEFT JOIN users p1 ON p1.id=g.p1_id LEFT JOIN users p2 ON p2.id=g.p2_id "
        "WHERE g.status IN ('finished','cancelled') ORDER BY g.id DESC LIMIT ?", (limit,)).fetchall():
        out.append(_hist_row("Piškvorky", g, g["p1n"], g["p2n"]))
    for d in conn.execute(
        "SELECT d.id, d.type, d.status, d.stake, d.winner, d.updated_at, d.created_at, "
        "p1.username AS p1n, p2.username AS p2n FROM duels d "
        "LEFT JOIN users p1 ON p1.id=d.p1_id LEFT JOIN users p2 ON p2.id=d.p2_id "
        "WHERE d.status IN ('finished','cancelled') ORDER BY d.id DESC LIMIT ?", (limit,)).fetchall():
        out.append(_hist_row(_DUEL_LABEL.get(d["type"], "Duel"), d, d["p1n"], d["p2n"], duel=True))
    out.sort(key=lambda x: (x["when"] or ""), reverse=True)
    return out[:limit]


def _refund_finished(conn, table: str, row, label: str) -> dict:
    """Refund dohrané 1v1 hry: storno výhry vítězi + vrácení vkladu oběma. Stav → cancelled.
    Pozor: může dát vítěze do mínusu (když už výhru utratil) – stejné jako u oprav predikcí."""
    if row["status"] != "finished":
        return {"ok": False, "error": f"#{row['id']} není dohraná (stav: {row['status']})."}
    if row["winner"] not in (1, 2):
        return {"ok": False, "error": "Refund jde jen u her s vítězem (remízy ne)."}
    stake, pot = row["stake"], row["stake"] * 2
    prize = pot - (pot * _rake_pct(conn) // 100)
    win_uid = row["p1_id"] if row["winner"] == 1 else row["p2_id"]
    add_points(conn, win_uid, -prize, f"{label} #{row['id']} – refund (storno výhry)")
    add_points(conn, row["p1_id"], stake, f"{label} #{row['id']} – refund vkladu")
    if row["p2_id"]:
        add_points(conn, row["p2_id"], stake, f"{label} #{row['id']} – refund vkladu")
    conn.execute(f"UPDATE {table} SET status='cancelled', updated_at=? WHERE id=?", (now_iso(), row["id"]))
    conn.commit()
    return {"ok": True, "id": row["id"]}


def refund_game_admin(conn, gid: int) -> dict:
    g = conn.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    return _refund_finished(conn, "games", g, "Piškvorky") if g else {"ok": False, "error": "Hra nenalezena."}


def refund_duel_admin(conn, did: int) -> dict:
    d = conn.execute("SELECT * FROM duels WHERE id=?", (did,)).fetchone()
    return _refund_finished(conn, "duels", d, _DUEL_LABEL.get(d["type"], "Duel")) if d else {"ok": False, "error": "Duel nenalezen."}


def _username(conn, uid):
    if not uid:
        return None
    r = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    return r["username"] if r else "?"


def _game_public(conn, g, me_id: int) -> dict:
    """Stav hry pro daného hráče (board jako pole 0/1/2)."""
    me_player = 1 if g["p1_id"] == me_id else (2 if g["p2_id"] == me_id else 0)
    can_timeout = False
    if g["status"] == "active" and me_player and g["turn"] != me_player:
        can_timeout = _seconds_since(g["last_move_at"]) > _move_limit(g)
    return {
        "id": g["id"], "type": g["type"], "status": g["status"], "stake": g["stake"],
        "pot": g["stake"] * (2 if g["status"] in ("active", "finished") else 1),
        "board": [0 if ch == "." else int(ch) for ch in g["board"]],
        "size": int(round(len(g["board"]) ** 0.5)), "win": WIN, "turn": g["turn"], "move_count": g["move_count"],
        "winner": g["winner"],
        "p1": {"id": g["p1_id"], "username": _username(conn, g["p1_id"])},
        "p2": {"id": g["p2_id"], "username": _username(conn, g["p2_id"])} if g["p2_id"] else None,
        "me_player": me_player,
        "your_turn": bool(me_player and g["status"] == "active" and g["turn"] == me_player),
        "can_claim_timeout": can_timeout,
        "move_timeout_s": _move_limit(g),
        "move_left_s": (max(0, _move_limit(g) - int(_seconds_since(g["last_move_at"]))) if g["status"] == "active" else 0),
        "game_max_s": GAME_MAX_S,
        "game_left_s": (max(0, GAME_MAX_S - int(_seconds_since(g["active_at"]))) if (g["status"] == "active" and g["active_at"]) else 0),
        "rake_pct": _rake_pct(conn),
    }


def _get_game(conn, gid):
    g = conn.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    if not g:
        raise HTTPException(status_code=404, detail="Hra nenalezena.")
    return g


def _finish(conn, g, winner: int) -> bool:
    """Ukončí hru a vyplatí. winner: 0=remíza, 1=p1, 2=p2. Vrací True když reálně dohrál tento request.

    Atomicky přepne active→finished a vyplácí JEN ten request, který přechod reálně provedl (rowcount==1).
    Bez toho dva souběžné claim-timeout (nebo timeout + vítězný tah) projdou stavovou kontrolou oba a banka
    se vyplatí víckrát (200 → 400). Po prvním přepnutí má každý další WHERE status='active' nesplněno."""
    if conn.execute("UPDATE games SET status='finished', winner=?, updated_at=? WHERE id=? AND status='active'",
                    (winner, now_iso(), g["id"])).rowcount == 0:
        return False     # hru už dohrál někdo jiný – nevyplácej znovu
    stake = g["stake"]
    if winner == 0:  # remíza → vrať oběma vklad
        add_points(conn, g["p1_id"], stake, f"Remíza v piškvorkách #{g['id']}")
        if g["p2_id"]:
            add_points(conn, g["p2_id"], stake, f"Remíza v piškvorkách #{g['id']}")
    else:
        pot = stake * 2
        rake = pot * _rake_pct(conn) // 100
        prize = pot - rake
        win_uid = g["p1_id"] if winner == 1 else g["p2_id"]
        add_points(conn, win_uid, prize, f"Výhra v piškvorkách #{g['id']}")
        lose_uid = g["p2_id"] if winner == 1 else g["p1_id"]
        note_game_net(conn, win_uid, prize - stake)      # denní strop grindu (net zisk)
        if lose_uid:
            note_game_net(conn, lose_uid, -stake)
    return True


def _resolve_timeouts(conn, g):
    """Auto-vyhodnotí timeouty: tah > MOVE_TIMEOUT NEBO celá hra > GAME_MAX_S → prohrává ten,
    kdo je na tahu. Vrací (případně dohranou) hru. Volá se při čtení stavu i před tahem."""
    if g["status"] != "active":
        return g
    over_move = _seconds_since(g["last_move_at"]) > _move_limit(g)
    over_game = g["active_at"] and _seconds_since(g["active_at"]) > GAME_MAX_S
    if over_move or over_game:
        winner = 2 if g["turn"] == 1 else 1      # kdo je na tahu (g["turn"]) prohrává
        _finish(conn, g, winner)
        conn.commit()
        return _get_game(conn, g["id"])
    return g


# ---------------- Lobby ----------------
@router.get("/open")
def list_open(user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    """Otevřené hry čekající na soupeře."""
    _expire_open(conn)
    rows = conn.execute(
        "SELECT g.*, u.username AS p1_name FROM games g JOIN users u ON u.id=g.p1_id "
        "WHERE g.status='open' ORDER BY g.id DESC LIMIT 50"
    ).fetchall()
    return [{
        "id": g["id"], "stake": g["stake"], "creator": g["p1_name"],
        "is_mine": g["p1_id"] == user["id"], "created_at": g["created_at"],
        "wait_s": int(_seconds_since(g["created_at"])),
    } for g in rows]


@router.get("/mine")
def list_mine(user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    """Moje běžící hry + posledních pár dohraných."""
    rows = conn.execute(
        "SELECT * FROM games WHERE (p1_id=? OR p2_id=?) AND status IN ('open','active') "
        "ORDER BY id DESC LIMIT 20", (user["id"], user["id"])
    ).fetchall()
    return [_game_public(conn, _resolve_timeouts(conn, g), user["id"]) for g in rows]


@router.post("/create")
def create_game(data: GameCreateIn, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    require_can_gamble(user)                # sebevyloučení ze sázek (Tipsport-style)
    if games_capped(conn, user):
        raise HTTPException(status_code=429, detail="Pro dnešek už máš denní strop ze her vyčerpaný 🎲 Zkus to prosím zítra.")
    if GAMES_MAINTENANCE:
        raise HTTPException(status_code=503, detail="Hry jsou v údržbě – brzy se vrátí (chystáme 9×9). 🔧")
    rate_limit(f"game:create:{user['id']}", 10, 60)
    # nedovol moc otevřených her naráz (escrow by zamkl body)
    openc = conn.execute(
        "SELECT COUNT(*) AS c FROM games WHERE p1_id=? AND status='open'", (user["id"],)
    ).fetchone()["c"]
    if openc >= 3:
        raise HTTPException(status_code=400, detail="Máš příliš mnoho otevřených her (nejvýše 3). Některou prosím zruš.")
    if data.stake < DUEL_MIN:                        # min PvP sázka (sdíleno s duely) – anti quest-abuse (1-sedlák piškvorky)
        raise HTTPException(status_code=400, detail=f"Minimální sázka je {DUEL_MIN} sedláků.")
    # atomický escrow – nejde do mínusu ani při souběhu
    check_wager_limit(conn, user, data.stake)        # responsible gaming: denní limit sázek
    if not try_debit(conn, user["id"], data.stake, "Sázka – piškvorky (vklad)"):
        raise HTTPException(status_code=400,
                            detail=f"Nemáš dostatek bodů. Sázka je {data.stake}, ty máš {user['points']}.")
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO games (type, status, stake, board, turn, p1_id, move_count, created_at, updated_at) "
        "VALUES ('gomoku','open',?,?,1,?,0,?,?)",
        (data.stake, _empty_board(), user["id"], ts, ts),
    )
    conn.commit()
    g = _get_game(conn, cur.lastrowid)
    return _game_public(conn, g, user["id"])


@router.post("/{gid}/join")
def join_game(gid: int, request: Request, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    require_can_gamble(user)                # sebevyloučení ze sázek (Tipsport-style)
    if games_capped(conn, user):
        raise HTTPException(status_code=429, detail="Pro dnešek už máš denní strop ze her vyčerpaný 🎲 Zkus to prosím zítra.")
    if GAMES_MAINTENANCE:
        raise HTTPException(status_code=503, detail="Hry jsou v údržbě – brzy se vrátí (chystáme 9×9). 🔧")
    rate_limit(f"game:join:{user['id']}", 20, 60)
    g = _get_game(conn, gid)
    if g["status"] != "open":
        raise HTTPException(status_code=400, detail="Tahle hra už není volná.")
    if g["p1_id"] == user["id"]:
        raise HTTPException(status_code=400, detail="Tohle je tvoje hra – počkej prosím na soupeře.")
    # anti-farma: dva NE-admin účty ze stejné IP/zařízení proti sobě nesmí
    p1 = conn.execute("SELECT role FROM users WHERE id=?", (g["p1_id"],)).fetchone()
    both_non_admin = user["role"] != ROLE_ADMIN and (not p1 or p1["role"] != ROLE_ADMIN)
    if both_non_admin and _same_person(conn, g["p1_id"], user["id"]):
        raise HTTPException(status_code=403,
                            detail="Nemůžeš hrát sám proti sobě (stejná IP nebo zařízení jako zakladatel).")
    # atomický escrow vkladu
    check_wager_limit(conn, user, g["stake"])        # responsible gaming: denní limit sázek
    if not try_debit(conn, user["id"], g["stake"], "Sázka – piškvorky (vklad)"):
        raise HTTPException(status_code=400,
                            detail=f"Nemáš dostatek bodů. Sázka je {g['stake']}, ty máš {user['points']}.")
    # obsazení slotu je atomické (WHERE status='open'); když to nezabralo, byl někdo rychlejší
    cur = conn.execute(
        "UPDATE games SET status='active', p2_id=?, last_move_at=?, active_at=?, updated_at=? WHERE id=? AND status='open'",
        (user["id"], now_iso(), now_iso(), now_iso(), gid),
    )
    if cur.rowcount == 0:
        add_points(conn, user["id"], g["stake"], f"Vrácení vkladu – hra #{gid} už obsazená")
        conn.commit()
        raise HTTPException(status_code=409, detail="Tahle hra už není volná – někdo byl rychlejší. Vklad ti vracíme. 🪙")
    conn.commit()
    g = _get_game(conn, gid)
    return _game_public(conn, g, user["id"])


@router.post("/{gid}/move")
def make_move(gid: int, data: GameMoveIn, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"game:move:{user['id']}", 60, 30)
    g = _resolve_timeouts(conn, _get_game(conn, gid))
    me = 1 if g["p1_id"] == user["id"] else (2 if g["p2_id"] == user["id"] else 0)
    if not me:
        raise HTTPException(status_code=403, detail="Nejsi hráčem téhle hry.")
    if g["status"] != "active":
        return _game_public(conn, g, user["id"])   # mezitím vypršel čas → vrať dohraný stav
    if g["turn"] != me:
        raise HTTPException(status_code=400, detail="Nejsi na tahu.")
    if _seconds_since(g["last_move_at"]) < MIN_MOVE_S:
        raise HTTPException(status_code=400, detail="Tah byl příliš rychlý – chvilku prosím počkej (ochrana proti botům).")
    cell = data.cell
    if cell < 0 or cell >= BOARD * BOARD:
        raise HTTPException(status_code=400, detail="Políčko je mimo hrací plochu.")
    board = g["board"]
    if board[cell] != ".":
        raise HTTPException(status_code=400, detail="Tohle políčko je už obsazené.")
    sym = str(me)
    board = board[:cell] + sym + board[cell + 1:]
    mc = g["move_count"] + 1
    if _check_win(board, cell, sym):
        conn.execute("UPDATE games SET board=?, move_count=?, last_move_at=?, updated_at=? WHERE id=?",
                     (board, mc, now_iso(), now_iso(), gid))
        g2 = _get_game(conn, gid)
        _finish(conn, g2, me)
        conn.commit()
        return _game_public(conn, _get_game(conn, gid), user["id"])
    if mc >= BOARD * BOARD:  # plná plocha → remíza
        conn.execute("UPDATE games SET board=?, move_count=?, last_move_at=?, updated_at=? WHERE id=?",
                     (board, mc, now_iso(), now_iso(), gid))
        _finish(conn, _get_game(conn, gid), 0)
        conn.commit()
        return _game_public(conn, _get_game(conn, gid), user["id"])
    nxt = 2 if me == 1 else 1
    conn.execute("UPDATE games SET board=?, turn=?, move_count=?, last_move_at=?, updated_at=? WHERE id=?",
                 (board, nxt, mc, now_iso(), now_iso(), gid))
    conn.commit()
    return _game_public(conn, _get_game(conn, gid), user["id"])


@router.get("/{gid}")
def get_game(gid: int, user: sqlite3.Row = Depends(require_user),
             conn: sqlite3.Connection = Depends(db_dep)):
    return _game_public(conn, _resolve_timeouts(conn, _get_game(conn, gid)), user["id"])


@router.post("/{gid}/cancel")
def cancel_game(gid: int, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    g = _get_game(conn, gid)
    if g["p1_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Zrušit hru může jen její zakladatel.")
    # Atomicky přepni open→cancelled; refund dostane JEN request, který přepnul (rowcount==1).
    # Bez toho N souběžných /cancel vrátí vklad N×.
    if conn.execute("UPDATE games SET status='cancelled', updated_at=? WHERE id=? AND status='open'",
                    (now_iso(), gid)).rowcount == 0:
        raise HTTPException(status_code=400, detail="Tuhle hru už nejde zrušit.")
    add_points(conn, g["p1_id"], g["stake"], f"Zrušená hra #{gid} – vrácení vkladu")
    conn.commit()
    return {"ok": True}


@router.post("/{gid}/claim-timeout")
def claim_timeout(gid: int, user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Soupeř dlouho netáhl → nárokuj výhru."""
    g = _get_game(conn, gid)
    if g["status"] != "active":
        raise HTTPException(status_code=400, detail="Hra právě neběží.")
    me = 1 if g["p1_id"] == user["id"] else (2 if g["p2_id"] == user["id"] else 0)
    if not me:
        raise HTTPException(status_code=403, detail="Nejsi hráčem téhle hry.")
    if g["turn"] == me:
        raise HTTPException(status_code=400, detail="Na tahu jsi ty, ne soupeř.")
    if _seconds_since(g["last_move_at"]) <= _move_limit(g):
        raise HTTPException(status_code=400, detail="Soupeř má na tah ještě čas.")
    _finish(conn, g, me)
    conn.commit()
    return _game_public(conn, _get_game(conn, gid), user["id"])


# ============================================================
#  DUELY 1v1 o bank – coinflip / kostky (okamžité vyhodnocení při přijetí výzvy)
# ============================================================
DUEL_TYPES = ("coinflip", "dice")
DUEL_MIN = 50           # min PvP sázka (duely I piškvorky) – anti quest-abuse na pvp_won quest
DUEL_MAX = 500_000
DUEL_OPEN_TTL_S = 30 * 60        # otevřená výzva bez soupeře expiruje za 30 min (refund)
_DUEL_LABEL = {"coinflip": "Coinflip duel", "dice": "Kostky duel", "rps": "Kámen-nůžky-papír"}


def _expire_open_duels(conn) -> None:
    rows = conn.execute("SELECT * FROM duels WHERE status='open'").fetchall()
    changed = False
    for d in rows:
        if _seconds_since(d["created_at"]) > DUEL_OPEN_TTL_S:
            add_points(conn, d["p1_id"], d["stake"], f"Vypršelá výzva (duel #{d['id']}) – vrácení vkladu")
            conn.execute("UPDATE duels SET status='cancelled', updated_at=? WHERE id=?", (now_iso(), d["id"]))
            changed = True
    if changed:
        conn.commit()


def _duel_public(conn, d, me_id: int) -> dict:
    state = json.loads(d["state"]) if d["state"] else {}
    return {
        "id": d["id"], "type": d["type"], "label": _DUEL_LABEL.get(d["type"], "Duel"),
        "status": d["status"], "stake": d["stake"], "pot": d["stake"] * 2,
        "winner": d["winner"], "state": state,
        "p1": {"id": d["p1_id"], "username": _username(conn, d["p1_id"])},
        "p2": {"id": d["p2_id"], "username": _username(conn, d["p2_id"])} if d["p2_id"] else None,
        "me_player": 1 if d["p1_id"] == me_id else (2 if d["p2_id"] == me_id else 0),
        "is_mine": d["p1_id"] == me_id,
        "created_at": d["created_at"], "wait_s": int(_seconds_since(d["created_at"])),
    }


def _resolve_duel(conn, did: int) -> None:
    """Vyhodnotí coinflip/dice duel a vyplatí vítěze (mínus rake). Volá se po obsazení slotu."""
    d = conn.execute("SELECT * FROM duels WHERE id=?", (did,)).fetchone()
    pot = d["stake"] * 2
    prize = pot - (pot * _rake_pct(conn) // 100)
    if d["type"] == "coinflip":
        flip = secure_choice(("heads", "tails"))      # heads = p1, tails = p2
        winner = 1 if flip == "heads" else 2
        state = {"coin": flip}
    else:                                              # dice – oba hodí d100, vyšší bere (remíza → přehoz)
        r1, r2 = secure_randint(1, 100), secure_randint(1, 100)
        while r1 == r2:
            r1, r2 = secure_randint(1, 100), secure_randint(1, 100)
        winner = 1 if r1 > r2 else 2
        state = {"roll1": r1, "roll2": r2}
    win_uid = d["p1_id"] if winner == 1 else d["p2_id"]
    add_points(conn, win_uid, prize, f"{_DUEL_LABEL.get(d['type'], 'Duel')} #{did} – výhra")
    lose_uid = d["p2_id"] if winner == 1 else d["p1_id"]
    note_game_net(conn, win_uid, prize - d["stake"])     # denní strop grindu (net zisk)
    if lose_uid:
        note_game_net(conn, lose_uid, -d["stake"])
    conn.execute("UPDATE duels SET status='finished', winner=?, state=?, updated_at=? WHERE id=?",
                 (winner, json.dumps(state), now_iso(), did))


@router.get("/duels/open")
def duels_open(type: str = "", user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    _expire_open_duels(conn)
    q, args = "SELECT * FROM duels WHERE status='open'", []
    if type in DUEL_TYPES:
        q += " AND type=?"
        args.append(type)
    rows = conn.execute(q + " ORDER BY id DESC LIMIT 50", args).fetchall()
    return [_duel_public(conn, d, user["id"]) for d in rows]


@router.get("/duels/mine")
def duels_mine(user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT * FROM duels WHERE (p1_id=? OR p2_id=?) ORDER BY id DESC LIMIT 8", (user["id"], user["id"])
    ).fetchall()
    return [_duel_public(conn, d, user["id"]) for d in rows]


@router.post("/duels/create")
def duel_create(data: DuelCreateIn, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    require_can_gamble(user)                # sebevyloučení ze sázek (Tipsport-style)
    if games_capped(conn, user):
        raise HTTPException(status_code=429, detail="Pro dnešek už máš denní strop ze her vyčerpaný 🎲 Zkus to prosím zítra.")
    rate_limit(f"duel:cd:{user['id']}", 1, 3)      # cooldown: max 1 duel akce za 3 s (anti-spam)
    rate_limit(f"duel:create:{user['id']}", 10, 60)
    if data.type not in DUEL_TYPES:
        raise HTTPException(status_code=400, detail="Neznámý typ duelu.")
    if data.stake < DUEL_MIN:
        raise HTTPException(status_code=400, detail=f"Minimální sázka je {DUEL_MIN} sedláků.")
    if data.stake > DUEL_MAX:
        raise HTTPException(status_code=400, detail=f"Maximální sázka je {DUEL_MAX} sedláků.")
    openc = conn.execute("SELECT COUNT(*) AS c FROM duels WHERE p1_id=? AND status='open'",
                         (user["id"],)).fetchone()["c"]
    if openc >= 3:
        raise HTTPException(status_code=400, detail="Máš příliš mnoho otevřených výzev (nejvýše 3). Některou prosím zruš.")
    check_wager_limit(conn, user, data.stake)        # responsible gaming: denní limit sázek
    if not try_debit(conn, user["id"], data.stake, f"{_DUEL_LABEL[data.type]} – vklad"):
        raise HTTPException(status_code=400,
                            detail=f"Nemáš dostatek bodů. Sázka je {data.stake}, ty máš {user['points']}.")
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO duels (type, status, stake, p1_id, created_at, updated_at) VALUES (?, 'open', ?, ?, ?, ?)",
        (data.type, data.stake, user["id"], ts, ts),
    )
    conn.commit()
    return _duel_public(conn, conn.execute("SELECT * FROM duels WHERE id=?", (cur.lastrowid,)).fetchone(), user["id"])


@router.post("/duels/{did}/join")
def duel_join(did: int, request: Request, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    require_can_gamble(user)                # sebevyloučení ze sázek (Tipsport-style)
    if games_capped(conn, user):
        raise HTTPException(status_code=429, detail="Pro dnešek už máš denní strop ze her vyčerpaný 🎲 Zkus to prosím zítra.")
    rate_limit(f"duel:cd:{user['id']}", 1, 3)      # cooldown: max 1 duel akce za 3 s (anti-spam)
    rate_limit(f"duel:join:{user['id']}", 20, 60)
    d = conn.execute("SELECT * FROM duels WHERE id=?", (did,)).fetchone()
    if not d:
        raise HTTPException(status_code=404, detail="Duel nenalezen.")
    if d["status"] != "open":
        raise HTTPException(status_code=400, detail="Tahle výzva už není volná.")
    if d["p1_id"] == user["id"]:
        raise HTTPException(status_code=400, detail="Tohle je tvoje výzva – počkej prosím na soupeře.")
    p1 = conn.execute("SELECT role FROM users WHERE id=?", (d["p1_id"],)).fetchone()
    both_non_admin = user["role"] != ROLE_ADMIN and (not p1 or p1["role"] != ROLE_ADMIN)
    if both_non_admin and _same_person(conn, d["p1_id"], user["id"]):
        raise HTTPException(status_code=403,
                            detail="Nemůžeš hrát sám proti sobě (stejná IP nebo zařízení jako vyzyvatel).")
    check_wager_limit(conn, user, d["stake"])        # responsible gaming: denní limit sázek
    if not try_debit(conn, user["id"], d["stake"], f"{_DUEL_LABEL[d['type']]} – vklad"):
        raise HTTPException(status_code=400,
                            detail=f"Nemáš dostatek bodů. Sázka je {d['stake']}, ty máš {user['points']}.")
    cur = conn.execute("UPDATE duels SET p2_id=?, status='active', updated_at=? WHERE id=? AND status='open'",
                       (user["id"], now_iso(), did))
    if cur.rowcount == 0:
        add_points(conn, user["id"], d["stake"], f"Duel #{did} už obsazen – vrácení vkladu")
        conn.commit()
        raise HTTPException(status_code=409, detail="Někdo byl rychlejší. Vklad ti vracíme. 🪙")
    _resolve_duel(conn, did)        # coinflip/dice se vyhodnotí hned
    conn.commit()
    return _duel_public(conn, conn.execute("SELECT * FROM duels WHERE id=?", (did,)).fetchone(), user["id"])


@router.post("/duels/{did}/cancel")
def duel_cancel(did: int, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    d = conn.execute("SELECT * FROM duels WHERE id=?", (did,)).fetchone()
    if not d:
        raise HTTPException(status_code=404, detail="Duel nenalezen.")
    if d["p1_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Zrušit výzvu může jen ten, kdo ji vytvořil.")
    # Atomicky open→cancelled; refund jen request, který přepnul (rowcount==1) – jinak N× vrácení vkladu.
    if conn.execute("UPDATE duels SET status='cancelled', updated_at=? WHERE id=? AND status='open'",
                    (now_iso(), did)).rowcount == 0:
        raise HTTPException(status_code=400, detail="Tuhle výzvu už nejde zrušit.")
    add_points(conn, d["p1_id"], d["stake"], f"Zrušená výzva (duel #{did}) – vrácení vkladu")
    conn.commit()
    return {"ok": True}


@router.get("/duels/{did}")
def duel_get(did: int, user: sqlite3.Row = Depends(require_user),
             conn: sqlite3.Connection = Depends(db_dep)):
    d = conn.execute("SELECT * FROM duels WHERE id=?", (did,)).fetchone()
    if not d:
        raise HTTPException(status_code=404, detail="Duel nenalezen.")
    return _duel_public(conn, d, user["id"])
