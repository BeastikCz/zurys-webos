"""Predikce – sázení bodů na výsledek (CS2 zápasy/eventy), à la Twitch Predictions.

Mechanika (pari-mutuel):
- Streamer vytvoří predikci = otázka + 2–4 možnosti (např. „Vyhrajeme zápas?" → Ano/Ne).
- Diváci, dokud je predikce OTEVŘENÁ, vsadí PTS na jednu možnost (vklad jde hned do escrow).
- Jeden divák sází jen na JEDNU možnost (částku může navyšovat, ale stranu nemění).
- Streamer predikci uzamkne (volitelné) a pak vyhodnotí = vybere vítěznou možnost.
- VÝPLATA: výherci si rozdělí CELÝ bank podle výše své sázky
  (výplata = sázka × celkový_bank / bank_vítězné_strany → vlastní vklad zpět + podíl z proher).
- Speciál: když na vítěznou stranu nikdo nevsadil → vrátí se všem (fér). Zrušení → vrátí se všem.

Escrow + atomický odečet (try_debit) brání mínusu i dvojímu odečtu. Výplata je idempotentní
přes status guard (jen ze stavu open/locked → resolved).
"""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import STAFF_ROLES
from ..db import now_iso
from ..deps import (db_dep, require_user, get_current_user, add_points, try_debit,
                    record_audit, can_access, require_can_gamble)
from ..models import PredictionCreateIn, PredictionBetIn, PredictionResolveIn
from ..ratelimit import rate_limit

router = APIRouter(prefix="/predictions", tags=["predictions"])


def require_pred_staff(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    """Správa predikcí: staff s oprávněním na sekci 'predictions' (admin vždy)."""
    if user["role"] not in STAFF_ROLES or not can_access(user["role"], "predictions"):
        raise HTTPException(status_code=403, detail="Na správu predikcí nemáš oprávnění.")
    return user


def _get_pred(conn, pid):
    p = conn.execute("SELECT * FROM predictions WHERE id = ?", (pid,)).fetchone()
    if not p:
        raise HTTPException(status_code=404, detail="Predikce nenalezena.")
    return p


def _pred_public(conn, p, me_id: Optional[int]) -> dict:
    """Stav predikce pro klienta: možnosti s banky, podíly, násobiči + moje sázka."""
    opts = conn.execute(
        "SELECT * FROM prediction_options WHERE prediction_id = ? ORDER BY position, id", (p["id"],)
    ).fetchall()
    pools, bettors = {}, {}
    for o in opts:
        r = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s, COUNT(*) AS c FROM prediction_bets WHERE option_id = ?",
            (o["id"],),
        ).fetchone()
        pools[o["id"]] = r["s"] or 0
        bettors[o["id"]] = r["c"] or 0
    total = sum(pools.values())

    my_bet = None
    if me_id:
        b = conn.execute(
            "SELECT option_id, amount, payout FROM prediction_bets WHERE prediction_id = ? AND user_id = ?",
            (p["id"], me_id),
        ).fetchone()
        if b:
            my_bet = {"option_id": b["option_id"], "amount": b["amount"], "payout": b["payout"]}

    options = []
    for o in opts:
        pool = pools[o["id"]]
        options.append({
            "id": o["id"], "label": o["label"], "pool": pool, "bettors": bettors[o["id"]],
            "share_pct": round(pool * 100 / total) if total else 0,
            # potenciální násobič, když tahle možnost vyhraje (celý bank / její bank)
            "mult": round(total / pool, 2) if pool > 0 else None,
            "is_winner": p["winner_option_id"] == o["id"],
        })
    return {
        "id": p["id"], "question": p["question"], "game": p["game"], "status": p["status"],
        "total_pool": total, "options": options, "my_bet": my_bet,
        "winner_option_id": p["winner_option_id"], "created_at": p["created_at"],
        "lock_at": (p["lock_at"] if "lock_at" in p.keys() else None),
    }


def _autolock_due(conn) -> int:
    """Lazy auto-lock: zamkne OPEN predikce, kterým vypršel lock_at (countdown sázek).
    NEJDŘÍV levný SELECT – UPDATE (write lock) JEN když fakt něco vypršelo. Jinak by každý
    poll /predictions (každý divák co 7 s) dělal write lock → z čtení zápis → contention
    a 500 „database is locked" přímo na /predictions. Teď je to v 99 % případů čistý read."""
    now = now_iso()
    due = conn.execute(
        "SELECT 1 FROM predictions WHERE status='open' AND lock_at IS NOT NULL AND lock_at <= ? LIMIT 1",
        (now,)).fetchone()
    if not due:
        return 0
    cur = conn.execute(
        "UPDATE predictions SET status='locked', locked_at=? "
        "WHERE status='open' AND lock_at IS NOT NULL AND lock_at <= ?", (now, now))
    conn.commit()
    return cur.rowcount


def _pred_announce(text: str) -> None:
    """Hláška o predikci do Kick chatu v BACKGROUND threadu s VLASTNÍM DB connection
    (jako autodrop daemon). Kick API je synchronní HTTP – kdyby běželo v request threadu
    na sdíleném conn, drží DB write lock + blokuje jediný worker → „database is locked"
    a výpadek (stalo se 2026-06-13). Thread = request se vrátí hned, nic nedrží."""
    def _bg():
        try:
            from ..db import get_conn
            from .. import kickbot
            c = get_conn()
            try:
                kickbot.send_message(c, text, kind="prediction")
            finally:
                c.close()
        except Exception:
            pass
    try:
        threading.Thread(target=_bg, daemon=True).start()
    except Exception:
        pass


# ======================= VEŘEJNÉ =======================
@router.get("")
def list_predictions(user: Optional[sqlite3.Row] = Depends(get_current_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Aktivní predikce (open/locked) + pár posledních vyhodnocených."""
    me_id = user["id"] if user else None
    _autolock_due(conn)                          # zavři sázky, kterým vypršel countdown
    active = conn.execute(
        "SELECT * FROM predictions WHERE status IN ('open','locked') ORDER BY id DESC"
    ).fetchall()
    recent = conn.execute(
        "SELECT * FROM predictions WHERE status IN ('resolved','cancelled') ORDER BY id DESC LIMIT 6"
    ).fetchall()
    return {
        "active": [_pred_public(conn, p, me_id) for p in active],
        "recent": [_pred_public(conn, p, me_id) for p in recent],
    }


@router.post("/{pid}/bet")
def place_bet(pid: int, data: PredictionBetIn,
              user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    """Vsadí (nebo navýší sázku) na možnost. Vklad jde hned do escrow."""
    require_can_gamble(user)                # sebevyloučení ze sázek (Tipsport-style)
    rate_limit(f"pred:bet:{user['id']}", 30, 60)
    _autolock_due(conn)                     # countdown mohl mezitím vypršet → zavři pozdní sázku
    p = _get_pred(conn, pid)
    if p["status"] != "open":
        raise HTTPException(status_code=400, detail="Sázky na tuhle predikci jsou už uzavřené.")
    opt = conn.execute(
        "SELECT * FROM prediction_options WHERE id = ? AND prediction_id = ?", (data.option_id, pid)
    ).fetchone()
    if not opt:
        raise HTTPException(status_code=400, detail="Neplatná možnost.")
    existing = conn.execute(
        "SELECT * FROM prediction_bets WHERE prediction_id = ? AND user_id = ?", (pid, user["id"])
    ).fetchone()
    if existing and existing["option_id"] != data.option_id:
        raise HTTPException(status_code=400,
                            detail="Už jsi vsadil na jinou možnost – nelze sázet na obě strany.")
    # atomický escrow – nejde do mínusu ani při souběhu
    if not try_debit(conn, user["id"], data.amount, f"Predikce #{pid} – sázka"):
        raise HTTPException(status_code=400,
                            detail=f"Nemáš dost bodů. Sázka {data.amount}, máš {user['points']}.")
    if existing:
        conn.execute("UPDATE prediction_bets SET amount = amount + ? WHERE id = ?",
                     (data.amount, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO prediction_bets (prediction_id, option_id, user_id, amount, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, data.option_id, user["id"], data.amount, now_iso()),
        )
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {"ok": True, "balance": fresh["points"],
            "prediction": _pred_public(conn, _get_pred(conn, pid), user["id"])}


# ======================= SPRÁVA (staff) =======================
@router.post("")
def create_prediction(data: PredictionCreateIn, request: Request,
                      staff: sqlite3.Row = Depends(require_pred_staff),
                      conn: sqlite3.Connection = Depends(db_dep)):
    ts = now_iso()
    lock_at = ((datetime.now(timezone.utc) + timedelta(seconds=data.lock_seconds)).isoformat()
               if data.lock_seconds > 0 else None)
    cur = conn.execute(
        "INSERT INTO predictions (question, game, status, created_by, created_at, lock_at) "
        "VALUES (?, ?, 'open', ?, ?, ?)",
        (data.question.strip(), (data.game or "CS2").strip(), staff["id"], ts, lock_at),
    )
    pid = cur.lastrowid
    for i, label in enumerate(data.options):
        conn.execute(
            "INSERT INTO prediction_options (prediction_id, label, position) VALUES (?, ?, ?)",
            (pid, label, i),
        )
    record_audit(conn, staff, request, "prediction.create", f"#{pid}", data.question[:80])
    conn.commit()
    q = data.question.strip()
    if data.lock_seconds > 0:
        mins = max(1, round(data.lock_seconds / 60))
        _pred_announce(f"🎯 Nová predikce: {q} — sázejte na zurys.live! Sázky se zavřou za {mins} min ⏳🌾")
    else:
        _pred_announce(f"🎯 Nová predikce: {q} — sázejte na zurys.live! 🌾")
    return _pred_public(conn, _get_pred(conn, pid), staff["id"])


@router.get("/admin/all")
def admin_all(staff: sqlite3.Row = Depends(require_pred_staff),
              conn: sqlite3.Connection = Depends(db_dep)):
    """Všechny predikce (i vyhodnocené) pro správu."""
    rows = conn.execute("SELECT * FROM predictions ORDER BY id DESC LIMIT 100").fetchall()
    return [_pred_public(conn, p, staff["id"]) for p in rows]


@router.post("/{pid}/lock")
def lock_prediction(pid: int, request: Request,
                    staff: sqlite3.Row = Depends(require_pred_staff),
                    conn: sqlite3.Connection = Depends(db_dep)):
    p = _get_pred(conn, pid)
    if p["status"] not in ("open", "locked"):
        raise HTTPException(status_code=400, detail="Tuhle predikci už nejde zamknout.")
    conn.execute("UPDATE predictions SET status = 'locked', locked_at = ? WHERE id = ?",
                 (now_iso(), pid))
    record_audit(conn, staff, request, "prediction.lock", f"#{pid}")
    conn.commit()
    return _pred_public(conn, _get_pred(conn, pid), staff["id"])


@router.post("/{pid}/unlock")
def unlock_prediction(pid: int, request: Request,
                      staff: sqlite3.Row = Depends(require_pred_staff),
                      conn: sqlite3.Connection = Depends(db_dep)):
    p = _get_pred(conn, pid)
    if p["status"] != "locked":
        raise HTTPException(status_code=400, detail="Predikce není zamčená.")
    conn.execute("UPDATE predictions SET status = 'open', locked_at = NULL, lock_at = NULL WHERE id = ?", (pid,))
    record_audit(conn, staff, request, "prediction.unlock", f"#{pid}")
    conn.commit()
    return _pred_public(conn, _get_pred(conn, pid), staff["id"])


@router.post("/{pid}/resolve")
def resolve_prediction(pid: int, data: PredictionResolveIn, request: Request,
                       staff: sqlite3.Row = Depends(require_pred_staff),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Vyhodnotí predikci – vybere vítěznou možnost a rozdělí bank výhercům."""
    p = _get_pred(conn, pid)
    if p["status"] in ("resolved", "cancelled"):
        raise HTTPException(status_code=400, detail="Predikce už je uzavřená.")
    opt = conn.execute(
        "SELECT * FROM prediction_options WHERE id = ? AND prediction_id = ?", (data.option_id, pid)
    ).fetchone()
    if not opt:
        raise HTTPException(status_code=400, detail="Neplatná vítězná možnost.")

    bets = conn.execute("SELECT * FROM prediction_bets WHERE prediction_id = ?", (pid,)).fetchall()
    total = sum(b["amount"] for b in bets)
    win_pool = sum(b["amount"] for b in bets if b["option_id"] == data.option_id)

    paid_winners = 0
    if total > 0 and win_pool == 0:
        # nikdo netipnul vítěze → vrať všem (jinak by všichni jen prohráli)
        for b in bets:
            add_points(conn, b["user_id"], b["amount"], f"Predikce #{pid} – nikdo netipnul, vráceno")
            conn.execute("UPDATE prediction_bets SET payout = ? WHERE id = ?", (b["amount"], b["id"]))
    else:
        for b in bets:
            if b["option_id"] == data.option_id:
                payout = b["amount"] * total // win_pool   # vlastní vklad zpět + podíl z proher
                add_points(conn, b["user_id"], payout, f"Predikce #{pid} – výhra")
                conn.execute("UPDATE prediction_bets SET payout = ? WHERE id = ?", (payout, b["id"]))
                paid_winners += 1
            else:
                conn.execute("UPDATE prediction_bets SET payout = 0 WHERE id = ?", (b["id"],))

    conn.execute(
        "UPDATE predictions SET status = 'resolved', winner_option_id = ?, resolved_at = ? WHERE id = ?",
        (data.option_id, now_iso(), pid),
    )
    record_audit(conn, staff, request, "prediction.resolve", f"#{pid}",
                 f"vítěz: {opt['label']} ({paid_winners} výherců, bank {total})")
    conn.commit()
    if total > 0:
        if win_pool == 0:
            _pred_announce(f"🎯 Výsledek: {p['question']} → ✅ {opt['label']}! Nikdo netrefil — vklady vráceny.")
        else:
            _pred_announce(f"🎯 Výsledek: {p['question']} → ✅ {opt['label']}! {paid_winners} výherců si rozdělilo {total} sedláků 🌾")
    return _pred_public(conn, _get_pred(conn, pid), staff["id"])


@router.post("/{pid}/reresolve")
def reresolve_prediction(pid: int, data: PredictionResolveIn, request: Request,
                         staff: sqlite3.Row = Depends(require_pred_staff),
                         conn: sqlite3.Connection = Depends(db_dep)):
    """OPRAVA špatně vyhodnocené predikce: vrátí staré výplaty a vyplatí znovu na správného vítěze.
    Funguje JEN na už 'resolved'. Může způsobit záporný zůstatek (kdo si špatnou výhru už utratil)."""
    p = _get_pred(conn, pid)
    if p["status"] != "resolved":
        raise HTTPException(status_code=400, detail="Re-resolve jde jen na už vyhodnocenou predikci.")
    opt = conn.execute(
        "SELECT * FROM prediction_options WHERE id = ? AND prediction_id = ?", (data.option_id, pid)
    ).fetchone()
    if not opt:
        raise HTTPException(status_code=400, detail="Neplatná vítězná možnost.")
    if data.option_id == p["winner_option_id"]:
        raise HTTPException(status_code=400, detail="Tahle možnost už je nastavená jako vítěz.")

    bets = conn.execute("SELECT * FROM prediction_bets WHERE prediction_id = ?", (pid,)).fetchall()
    # 1) STORNO starých výplat (vrátíme stav do escrow – každý je zase jen mínus svůj vklad)
    for b in bets:
        if b["payout"]:
            add_points(conn, b["user_id"], -b["payout"], f"Predikce #{pid} – oprava: storno špatné výplaty")
        conn.execute("UPDATE prediction_bets SET payout = 0 WHERE id = ?", (b["id"],))

    # 2) Výplata znovu na správného vítěze (stejná pari-mutuel logika jako resolve)
    total = sum(b["amount"] for b in bets)
    win_pool = sum(b["amount"] for b in bets if b["option_id"] == data.option_id)
    paid_winners = 0
    if total > 0 and win_pool == 0:
        for b in bets:
            add_points(conn, b["user_id"], b["amount"], f"Predikce #{pid} – nikdo netipnul, vráceno")
            conn.execute("UPDATE prediction_bets SET payout = ? WHERE id = ?", (b["amount"], b["id"]))
    else:
        for b in bets:
            if b["option_id"] == data.option_id:
                payout = b["amount"] * total // win_pool
                add_points(conn, b["user_id"], payout, f"Predikce #{pid} – výhra (oprava)")
                conn.execute("UPDATE prediction_bets SET payout = ? WHERE id = ?", (payout, b["id"]))
                paid_winners += 1

    old = conn.execute("SELECT label FROM prediction_options WHERE id = ?", (p["winner_option_id"],)).fetchone()
    conn.execute("UPDATE predictions SET winner_option_id = ?, resolved_at = ? WHERE id = ?",
                 (data.option_id, now_iso(), pid))
    record_audit(conn, staff, request, "prediction.reresolve", f"#{pid}",
                 f"OPRAVA: {old['label'] if old else '?'} → {opt['label']} ({paid_winners} výherců, bank {total})")
    conn.commit()
    return _pred_public(conn, _get_pred(conn, pid), staff["id"])


@router.post("/{pid}/cancel")
def cancel_prediction(pid: int, request: Request,
                      staff: sqlite3.Row = Depends(require_pred_staff),
                      conn: sqlite3.Connection = Depends(db_dep)):
    """Zruší predikci a vrátí všem vklady."""
    p = _get_pred(conn, pid)
    if p["status"] in ("resolved", "cancelled"):
        raise HTTPException(status_code=400, detail="Predikce už je uzavřená.")
    bets = conn.execute("SELECT * FROM prediction_bets WHERE prediction_id = ?", (pid,)).fetchall()
    for b in bets:
        add_points(conn, b["user_id"], b["amount"], f"Predikce #{pid} zrušena – vráceno")
        conn.execute("UPDATE prediction_bets SET payout = ? WHERE id = ?", (b["amount"], b["id"]))
    conn.execute("UPDATE predictions SET status = 'cancelled', resolved_at = ? WHERE id = ?",
                 (now_iso(), pid))
    record_audit(conn, staff, request, "prediction.cancel", f"#{pid}", f"vráceno {len(bets)} sázek")
    conn.commit()
    return {"ok": True, "refunded": len(bets)}
