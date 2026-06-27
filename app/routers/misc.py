"""Leaderboard, redeem kódů a profil (objednávky + historie bodů)."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..anticheat import (check_or_block, is_new_account, new_account_redeem_pts,
                          NEW_ACCOUNT_MAX_REDEEM_PTS, GIFT_MIN_AGE_HOURS)
from ..config import ORDER_PENDING, UNLIMITED_STOCK, ROLE_ADMIN
from ..db import now_iso, get_setting
from ..deps import (db_dep, require_user, add_points, try_debit, record_audit, client_ip,
                    user_rank, tier_for_rank, self_excluded_until, level_info)
from ..models import (RedeemIn, TradeUrlIn, GiftIn, QuestClaimIn, CosmeticIn, FairSeedIn, SelfExcludeIn,
                      ProfileBioIn, WagerLimitIn, ModApplyIn, BattlePassClaimIn, LoginCalClaimIn,
                      GardenPlantIn, GardenPlantAllIn, GardenHarvestIn, DecorBuyIn, LevelPassClaimIn,
                      EggClaimIn)
from ..services import product_public, role_allows
from ..ratelimit import rate_limit
from ..security import secure_weighted_choice
from .. import economy, live, partners, cosmetics, fairness

router = APIRouter(tags=["misc"])

# Easter egg „Tajný sedlák" – tajné slovo se ověřuje SERVER-SIDE (na frontu je jen obfuskovaný hash
# pro detekci, ne plaintext). Změna slova = uprav EGG_WORD + char-codes _EGG na frontu (app.js).
EGG_WORD = "ZLATEVEJCE"
EGG_REWARD = 1500            # jednorázová malá odměna (ne ekonomika), gate 1×/uživatel
EGG_RIDDLE = "Co snese zlatá slepice?"
EGG_HINT = "Odpověď napiš jedním slovem — bez mezer a háčků — kdekoliv na webu (jen piš písmena). Tím chytíš Tajného sedláka. 🌾"
# Easter egg NENÍ nafurt – objeví se na náhodném místě jen v náhodných oknech (server rozhoduje).
# Vajíčko se ukáže ~EGG_WINDOWS_PER_HOUR×/h na EGG_WINDOW_MIN min, pak zmizí; mimo okno claim → {locked}. Tunable.
EGG_WINDOWS_PER_HOUR = 1     # kolikrát za hodinu se vajíčko objeví
EGG_WINDOW_MIN = 5          # jak dlouho je vidět (min), pak zmizí (~1h pauza, náhodná minuta)


def egg_window_active(now=None) -> bool:
    """Je teď aktivní náhodné okno pro easter egg? Start-minuty oken se deterministicky odvozují
    z hodiny (md5) → nepredikovatelné časy, ale stabilní (server i klient dají stejnou odpověď)."""
    now = now or datetime.now(timezone.utc)
    hkey = now.strftime("%Y%m%d%H")
    span = max(1, 60 - EGG_WINDOW_MIN)
    cur = now.minute + now.second / 60.0
    for i in range(EGG_WINDOWS_PER_HOUR):
        start = int.from_bytes(hashlib.md5(f"egg-win:{hkey}:{i}".encode()).digest()[:4], "big") % span
        if start <= cur < start + EGG_WINDOW_MIN:
            return True
    return False


@router.get("/egg/active")
def egg_active():
    """Je teď aktivní náhodné okno eggu? Frontend podle toho ukáže/skryje stopu 🥚 (jen v okně)."""
    return {"active": egg_window_active()}


@router.get("/egg/clue")
def egg_clue(user: sqlite3.Row = Depends(require_user)):
    """Hádanka k easter eggu – SERVER-SIDE (ne v JS), aby F12 inspekce JS neprozradila řešení."""
    return {"riddle": EGG_RIDDLE, "hint": EGG_HINT}


@router.post("/egg/claim")
def egg_claim(data: EggClaimIn, user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    """Easter egg: ověří slovo (server-side) + jen v aktivním NÁHODNÉM okně + atomický gate 1×/uživatel."""
    if (data.word or "").strip().upper() != EGG_WORD:
        raise HTTPException(status_code=400, detail="🥚?")          # vágně, žádná nápověda
    row = conn.execute("SELECT egg_found_at FROM users WHERE id = ?", (user["id"],)).fetchone()
    if row and row["egg_found_at"]:
        return {"already": True}                                    # už našel (vždy řekni, i mimo okno)
    if not egg_window_active():
        return {"locked": True}                                     # mimo okno – „vajíčko zrovna spí"
    cur = conn.execute("UPDATE users SET egg_found_at = ? WHERE id = ? AND egg_found_at IS NULL",
                       (now_iso(), user["id"]))
    if cur.rowcount == 0:
        conn.commit()
        return {"already": True}
    add_points(conn, user["id"], EGG_REWARD, "🥚 Tajný sedlák (easter egg)", xp=False)
    conn.commit()
    return {"found": True, "reward": EGG_REWARD}


@router.post("/me/self-exclude")
def me_self_exclude(data: SelfExcludeIn, user: sqlite3.Row = Depends(require_user),
                    conn: sqlite3.Connection = Depends(db_dep)):
    """Tipsport-style sebevyloučení ze sázek. NEJDE zrušit ani zkrátit – jen prodloužit/zpřísnit."""
    days = {"1d": 1, "7d": 7, "30d": 30}
    if data.duration == "perm":
        newval = "permanent"
    elif data.duration in days:
        newval = (datetime.now(timezone.utc) + timedelta(days=days[data.duration])).isoformat()
    else:
        raise HTTPException(status_code=400, detail="Neplatná délka vyloučení.")
    cur = self_excluded_until(user)
    if cur == "permanent":
        raise HTTPException(status_code=400, detail="Už máš trvalé sebevyloučení, to už nelze měnit.")
    if cur and newval != "permanent" and newval <= cur:
        raise HTTPException(status_code=400, detail="Sebevyloučení nejde zkrátit ani zrušit, jde ho jen prodloužit. 🔒")
    conn.execute("UPDATE users SET gamble_block_until = ? WHERE id = ?", (newval, user["id"]))
    conn.commit()
    return {"gamble_block_until": newval}


@router.get("/news")
def list_news(conn: sqlite3.Connection = Depends(db_dep)):
    """Veřejný changelog – publikované novinky, nejnovější první."""
    rows = conn.execute(
        "SELECT id, title, body, tag, created_at FROM patch_notes "
        "WHERE published = 1 ORDER BY created_at DESC, id DESC LIMIT 60"
    ).fetchall()
    return {"notes": [dict(r) for r in rows]}


@router.get("/stream/status")
def stream_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Veřejný stav streamu pro „živou tečku" v hlavičce (zelená = online, červená = offline).
    Respektuje admin override (on/off/auto) a 45s cache v live modulu."""
    return {"live": live.is_live(conn), "channel": live.broadcaster_slug(conn)}


@router.get("/leaderboard")
def leaderboard(limit: int = Query(50, ge=1, le=200),
                conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT id, username, avatar_url, points, role, is_sub, is_vip, is_og, egg_found_at, earned_total, "
        "cos_name, cos_frame, cos_banner, prestige FROM users "
        "ORDER BY points DESC, username ASC LIMIT ?",
        (limit,),
    ).fetchall()
    # ▲▼ pohyby z denních snapshotů (den/týden zpět). Chybí-li snapshot, delta = None (žádná šipka).
    ysnap, wsnap = {}, {}
    try:
        from datetime import date, timedelta
        yday = (date.today() - timedelta(days=1)).isoformat()
        wago = (date.today() - timedelta(days=7)).isoformat()
        ysnap = {r["user_id"]: r["rank"] for r in conn.execute(
            "SELECT user_id, rank FROM rank_snapshots WHERE day = ?", (yday,))}
        wsnap = {r["user_id"]: r["rank"] for r in conn.execute(
            "SELECT user_id, rank FROM rank_snapshots WHERE day = ?", (wago,))}
    except Exception:
        pass
    out, best_id, best_gain = [], None, 0
    for i, r in enumerate(rows):
        cur = i + 1
        delta = (ysnap[r["id"]] - cur) if r["id"] in ysnap else None
        if r["id"] in wsnap and (wsnap[r["id"]] - cur) > best_gain:
            best_gain, best_id = wsnap[r["id"]] - cur, r["id"]
        out.append({
            "rank": cur,
            "username": r["username"],
            "avatar_url": r["avatar_url"],
            "points": r["points"],
            "role": r["role"],
            "is_sub": bool(r["is_sub"]),
            "is_vip": bool(r["is_vip"]),
            "is_og": bool(r["is_og"]),
            "egg_found": bool(r["egg_found_at"] if "egg_found_at" in r.keys() else None),
            "cos": cosmetics.resolve(r),
            "delta": delta,
            "climber": False,
            "prestige": (r["prestige"] if "prestige" in r.keys() else 0) or 0,
            "level": level_info(r["earned_total"] if "earned_total" in r.keys() else 0)["level"],
        })
    if best_id is not None and best_gain >= 3:            # „stoupá týdne" jen při reálném skoku (≥3 pozice)
        for row, r in zip(out, rows):
            if r["id"] == best_id:
                row["climber"] = True
    return out


_weekly_cache = {"at": 0.0, "data": None}
_WEEKLY_TTL = 60   # s – board se nemusí počítat při každém pollu (jen scan na čtení)


@router.get("/leaderboard/weekly")
def leaderboard_weekly(conn: sqlite3.Connection = Depends(db_dep)):
    """Žebříček: kdo NASBÍRAL nejvíc sedláků tento týden. ČISTĚ READ-ONLY z points_log –
    NEsahá na users.points, NIC neresetuje. „Týden" = jen filtr created_at >= pondělí 00:00.
    Cache 60 s (čtení nelockuje zápis, ale ať se velký points_log neskenuje pořád)."""
    import time
    from datetime import datetime, timezone, timedelta
    nowm = time.monotonic()
    if _weekly_cache["data"] is not None and nowm - _weekly_cache["at"] < _WEEKLY_TTL:
        return _weekly_cache["data"]
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday.isoformat()
    rows = conn.execute(
        "SELECT u.id, u.username, u.avatar_url, u.role, u.is_sub, u.is_vip, u.is_og, "
        "  u.cos_name, u.cos_frame, u.cos_banner, SUM(l.change) AS gained "
        "FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.change > 0 AND l.created_at >= ? "
        "GROUP BY l.user_id ORDER BY gained DESC, u.username ASC LIMIT 50",
        (start,),
    ).fetchall()
    data = {
        "week_start": start,
        "rows": [{
            "rank": i + 1, "username": r["username"], "avatar_url": r["avatar_url"],
            "gained": r["gained"], "role": r["role"],
            "is_sub": bool(r["is_sub"]), "is_vip": bool(r["is_vip"]), "is_og": bool(r["is_og"]),
            "cos": cosmetics.resolve(r),
        } for i, r in enumerate(rows)],
    }
    _weekly_cache.update(at=nowm, data=data)
    return data


_season_cache = {"at": 0.0, "data": None}


@router.get("/leaderboard/season")
def leaderboard_season(conn: sqlite3.Connection = Depends(db_dep)):
    """Sezónní žebříček: kdo NASBÍRAL nejvíc sedláků tento MĚSÍC (reset 1. dne v měsíci).
    Read-only z points_log – nic neresetuje, zůstatky zůstávají. Cache 60 s."""
    import time
    from datetime import datetime, timezone
    nowm = time.monotonic()
    if _season_cache["data"] is not None and nowm - _season_cache["at"] < _WEEKLY_TTL:
        return _season_cache["data"]
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = conn.execute(
        "SELECT u.id, u.username, u.avatar_url, u.role, u.is_sub, u.is_vip, u.is_og, "
        "  u.cos_name, u.cos_frame, u.cos_banner, SUM(l.change) AS gained "
        "FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.change > 0 AND l.created_at >= ? "
        "GROUP BY l.user_id ORDER BY gained DESC, u.username ASC LIMIT 50",
        (start,),
    ).fetchall()
    data = {
        "season": now.strftime("%Y-%m"),
        "rows": [{
            "rank": i + 1, "username": r["username"], "avatar_url": r["avatar_url"],
            "gained": r["gained"], "role": r["role"],
            "is_sub": bool(r["is_sub"]), "is_vip": bool(r["is_vip"]), "is_og": bool(r["is_og"]),
            "cos": cosmetics.resolve(r),
        } for i, r in enumerate(rows)],
    }
    _season_cache.update(at=nowm, data=data)
    return data


@router.get("/profile/public")
def public_profile(nick: str = Query("", max_length=64),
                   viewer: sqlite3.Row = Depends(require_user),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Veřejný profil uživatele se statistikami (pro každého přihlášeného). Bez IP/e-mailu."""
    key = (nick or "").strip().lstrip("@").lower()
    if not key:
        raise HTTPException(status_code=400, detail="Zadej prosím uživatele. 🙂")
    u = conn.execute(
        "SELECT * FROM users WHERE LOWER(username) = ? OR LOWER(kick_username) = ? "
        "ORDER BY (kick_username IS NOT NULL) DESC LIMIT 1", (key, key),
    ).fetchone()
    if not u:
        raise HTTPException(status_code=404, detail="Takového uživatele jsme nenašli. 🤔")
    uid = u["id"]
    rank = user_rank(conn, u["points"], u["username"])
    league_key, league_mult = tier_for_rank(rank)
    earned = conn.execute("SELECT COALESCE(SUM(change),0) c FROM points_log WHERE user_id=? AND change>0", (uid,)).fetchone()["c"]
    spent = conn.execute("SELECT COALESCE(SUM(-change),0) c FROM points_log WHERE user_id=? AND change<0", (uid,)).fetchone()["c"]
    biggest = conn.execute("SELECT COALESCE(MAX(change),0) c FROM points_log WHERE user_id=? AND change>0", (uid,)).fetchone()["c"]
    from ..deps import earn_factor
    farm_gross = farm_xp = gambling_gross = 0
    for r in conn.execute("SELECT change, reason FROM points_log WHERE user_id=? AND change>0", (uid,)):
        factor = earn_factor(r["reason"])
        if factor > 0:
            farm_gross += r["change"]
            farm_xp += int(round(r["change"] * factor))
        else:
            gambling_gross += r["change"]
    gambling_spent = conn.execute(
        "SELECT COALESCE(SUM(-change),0) c FROM points_log WHERE user_id=? AND change<0 "
        "AND (lower(reason) LIKE '%mines%' OR lower(reason) LIKE '%blackjack%' "
        "OR lower(reason) LIKE '%predikce%' OR lower(reason) LIKE '%duel%' "
        "OR lower(reason) LIKE '%piĹˇkvor%' OR lower(reason) LIKE '%coinflip%')",
        (uid,)).fetchone()["c"]
    garden_row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN change>0 THEN change ELSE 0 END),0) gained, "
        "COALESCE(SUM(CASE WHEN change<0 THEN -change ELSE 0 END),0) spent, "
        "COALESCE(SUM(change),0) net FROM points_log WHERE user_id=? AND "
        "(lower(reason) LIKE 'sklizeĹ:%' OR lower(reason) LIKE 'zasazenĂ­:%' "
        "OR lower(reason) LIKE 'zĂˇchrana:%' OR lower(reason) LIKE 'dekorace zahrĂˇdky%')",
        (uid,)).fetchone()
    from ..econ_health import categorize
    farm_gross = farm_xp = gambling_gross = gambling_spent = 0
    garden_gained = garden_spent = garden_net = 0
    for r in conn.execute("SELECT change, reason FROM points_log WHERE user_id=?", (uid,)):
        change = r["change"]
        reason = r["reason"]
        cat = categorize(reason)[0]
        if change > 0:
            factor = earn_factor(reason)
            if factor > 0:
                farm_gross += change
                farm_xp += int(round(change * factor))
            else:
                gambling_gross += change
            if cat == "garden_h":
                garden_gained += change
        elif change < 0:
            spent_abs = -change
            if cat in ("mines", "games", "blackjack", "predictions"):
                gambling_spent += spent_abs
            if cat in ("garden_s", "garden_d"):
                garden_spent += spent_abs
        if cat in ("garden_h", "garden_s", "garden_d"):
            garden_net += change
    lvl = level_info(u["earned_total"] if "earned_total" in u.keys() else 0)   # úroveň z lifetime XP (ne z gross earned)
    g = conn.execute(
        "SELECT COUNT(*) AS played, "
        "COALESCE(SUM(CASE WHEN (winner=1 AND p1_id=?) OR (winner=2 AND p2_id=?) THEN 1 ELSE 0 END),0) AS won "
        "FROM duels WHERE status='finished' AND winner IN (1,2) AND (p1_id=? OR p2_id=?)",
        (uid, uid, uid, uid),
    ).fetchone()
    played, won = g["played"] or 0, g["won"] or 0
    raffle_wins = conn.execute("SELECT COUNT(*) AS c FROM raffle_winners WHERE user_id=?", (uid,)).fetchone()["c"]
    from ..achievements import BADGES
    earned_b = {r["badge_key"]: r["tier"] for r in conn.execute(
        "SELECT badge_key, tier FROM user_badges WHERE user_id=?", (uid,))}
    badges = [{"key": b["key"], "emoji": b["emoji"], "name": b["name"], "desc": b["desc"],
               "max_tier": len(b["tiers"]), "tier": earned_b.get(b["key"], 0),
               "earned": earned_b.get(b["key"], 0) > 0} for b in BADGES]
    # Vitrína: vyhrané (tomboly) + vlastněné odměny S OBRÁZKEM (dedup dle produktu, max 12).
    # Tomboly mají přednost (trofeje) a nesou flag won=True; pak vlastněné z objednávek.
    showcase = []
    seen = set()
    for r in conn.execute(
        "SELECT p.id AS pid, p.name, p.image_url, p.rarity, p.type, w.created_at "
        "FROM raffle_winners w JOIN products p ON p.id = w.product_id "
        "WHERE w.user_id = ? ORDER BY w.created_at DESC, w.id DESC", (uid,)):
        if r["pid"] in seen or not (r["image_url"] or "").strip():
            continue
        seen.add(r["pid"])
        showcase.append({"name": r["name"], "image_url": r["image_url"], "rarity": r["rarity"],
                         "type": r["type"], "created_at": r["created_at"], "won": True})
    for r in conn.execute(
        "SELECT p.id AS pid, COALESCE(p.name, o.product_name) AS name, p.image_url, p.rarity, p.type, o.created_at "
        "FROM orders o JOIN products p ON p.id = o.product_id "
        "WHERE o.user_id = ? AND p.image_url IS NOT NULL AND p.image_url != '' "
        "ORDER BY o.created_at DESC, o.id DESC", (uid,)):
        if r["pid"] in seen:
            continue
        seen.add(r["pid"])
        showcase.append({"name": r["name"], "image_url": r["image_url"], "rarity": r["rarity"],
                         "type": r["type"], "created_at": r["created_at"], "won": False})
    showcase = showcase[:12]
    from .. import garden as _g
    _decor_owned = {r["decor_key"] for r in conn.execute("SELECT decor_key FROM garden_decor WHERE user_id = ?", (uid,))}
    garden_decor = [d["icon"] for d in _g.DECOR if d["key"] in _decor_owned]
    garden_plots = _g.N_PLOTS + sum(1 for k in _g.PLOT_DECORS if k in _decor_owned)
    return {
        "id": uid,
        "username": u["username"], "avatar_url": u["avatar_url"] or "", "role": u["role"],
        "created_at": u["created_at"], "points": u["points"],
        "rank": rank, "league": league_key, "league_mult": league_mult,
        "earned_total": earned, "spent_total": spent, "biggest_win": biggest,
        "farm_gross_total": farm_gross, "farm_xp_total": farm_xp,
        "gambling_gross_total": gambling_gross,
        "gambling_spent_total": gambling_spent,
        "gambling_net_total": gambling_gross - gambling_spent,
        "garden_gained_total": garden_gained,
        "garden_spent_total": garden_spent,
        "garden_net_total": garden_net,
        "garden_decor": garden_decor, "garden_plots": garden_plots,
        "level": lvl["level"], "level_pct": lvl["pct"], "level_into": lvl["into"], "level_span": lvl["span"],
        "games_played": played, "games_won": won,
        "win_rate": round(won / played, 3) if played else 0,
        "raffle_wins": raffle_wins, "badges": badges, "showcase": showcase, "cos": cosmetics.resolve(u),
        "is_sub": bool(u["is_sub"]), "is_vip": bool(u["is_vip"]), "is_og": bool(u["is_og"]),
        "daily_streak": (u["daily_streak"] if "daily_streak" in u.keys() else 0) or 0,
        "bio": (u["bio"] if "bio" in u.keys() else "") or "",
        "fav_game": (u["fav_game"] if "fav_game" in u.keys() else "") or "",
        "prestige": (u["prestige"] if "prestige" in u.keys() else 0) or 0,
    }


@router.get("/community-goal")
def community_goal_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Stav komunitního chat cíle (veřejné – pro lištu na webu)."""
    from ..community_goal import status
    return status(conn)


@router.get("/sub-goal")
def sub_goal_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Stav komunitního SUB cíle (veřejné – pro lištu na webu)."""
    from ..subgoal import status
    return status(conn)


@router.get("/top-gifters")
def top_gifters_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Top gifteři aktuální session (veřejné – pro stream overlay spotlight)."""
    from ..subgoal import top_gifters
    g = top_gifters(conn, 5)
    return {"gifters": g, "count": len(g)}


@router.get("/recent-gifts")
def recent_gifts_status(since: int = None, conn: sqlite3.Connection = Depends(db_dep)):
    """Nové gift sub eventy s id > since (pro jednorázový gift-alert overlay).
    Bez since → jen baseline latest_id (overlay si ustaví výchozí bod, nehlásí staré gifty)."""
    from ..subgoal import recent_gifts
    return recent_gifts(conn, since)


@router.get("/recent-events")
def recent_events_status(since: int = None, conn: sqlite3.Connection = Depends(db_dep)):
    """Nové sub-typ eventy (new/resub/gift) s id > since (pro sjednocený alert overlay alerts.html).
    Bez since → baseline latest_id. kind: 'gift'|'resub'|'new'."""
    from ..subgoal import recent_events
    return recent_events(conn, since)


@router.get("/happy-hour")
def happy_hour_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Stav Happy Hour (veřejné – pro overlay/lištu). Jedno okno `happy_until`, víc perků:
    ×mult za sledování/chat, sleva na shop %, 2× za subs – overlay vypíše co je zrovna aktivní."""
    from .. import live_events
    from ..services import shop_discount_pct, sub_points_mult
    until = get_setting(conn, "happy_until", "") or ""
    active, seconds_left = False, 0
    if until:
        try:
            t = datetime.fromisoformat(until)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            delta = (t - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                active, seconds_left = True, int(delta)
        except Exception:
            pass
    watch_mult = live_events.happy_mult(conn)
    return {"active": active, "active_until": until, "seconds_left": seconds_left,
            "mult": watch_mult, "watch_mult": watch_mult,           # ×mult za sledování/chat (1.0 = ne)
            "shop_pct": shop_discount_pct(conn),                    # sleva na shop v % (0 = ne)
            "sub_2x": sub_points_mult(conn) >= 2}                   # 2× body za subs?


# ---------------- Osobní herní staty ----------------
@router.get("/me/game-stats")
def my_game_stats(user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Souhrn hráče napříč hrami: vsazeno / vyhráno / net / win rate / nej výhra / počet her."""
    uid = user["id"]

    # Mines
    m = conn.execute(
        "SELECT COUNT(*) g, COALESCE(SUM(bet),0) w, COALESCE(SUM(payout),0) p, COALESCE(MAX(payout),0) mx, "
        "SUM(CASE WHEN status='cashed' THEN 1 ELSE 0 END) cashed "
        "FROM mines_games WHERE user_id=? AND status IN ('busted','cashed')", (uid,)).fetchone()
    mines = {"games": m["g"], "wagered": m["w"], "won": m["p"], "net": m["p"] - m["w"],
             "biggest": m["mx"], "win_rate": round(m["cashed"] * 100 / m["g"]) if m["g"] else 0}

    # PvP – coinflip/dice/rps duely + piškvorky (stake = vklad každého; vítěz net +stake, poražený -stake)
    pvp = {"games": 0, "won": 0, "lost": 0, "wagered": 0, "net": 0, "biggest": 0}
    for tbl in ("duels", "games"):
        for r in conn.execute(
                f"SELECT p1_id, p2_id, stake, winner FROM {tbl} "
                f"WHERE (p1_id=? OR p2_id=?) AND status='finished' AND winner IN (0,1,2)", (uid, uid)):
            side = 1 if r["p1_id"] == uid else 2
            pvp["games"] += 1
            pvp["wagered"] += r["stake"]
            if r["winner"] == side:
                pvp["won"] += 1; pvp["net"] += r["stake"]; pvp["biggest"] = max(pvp["biggest"], r["stake"])
            elif r["winner"] in (1, 2):
                pvp["lost"] += 1; pvp["net"] -= r["stake"]
            # winner == 0 → remíza, net 0
    pvp["win_rate"] = round(pvp["won"] * 100 / (pvp["won"] + pvp["lost"])) if (pvp["won"] + pvp["lost"]) else 0

    # Blackjack – solo + živý stůl
    bj = conn.execute(
        "SELECT COUNT(*) g, COALESCE(SUM(bet),0) w, COALESCE(SUM(payout),0) p, COALESCE(MAX(payout),0) mx "
        "FROM blackjack_games WHERE user_id=? AND status='done'", (uid,)).fetchone()
    bjs = conn.execute(
        "SELECT COUNT(*) g, COALESCE(SUM(bet),0) w, COALESCE(SUM(payout),0) p "
        "FROM bj_seats WHERE user_id=? AND state='resolved'", (uid,)).fetchone()
    bg, bw, bp = bj["g"] + bjs["g"], bj["w"] + bjs["w"], bj["p"] + bjs["p"]
    blackjack = {"games": bg, "wagered": bw, "won": bp, "net": bp - bw, "biggest": bj["mx"]}

    # Predikce – jen z vyhodnocených (payout dosazen)
    pr = conn.execute(
        "SELECT COUNT(*) g, COALESCE(SUM(pb.amount),0) w, COALESCE(SUM(pb.payout),0) p, COALESCE(MAX(pb.payout),0) mx "
        "FROM prediction_bets pb JOIN predictions p ON p.id=pb.prediction_id "
        "WHERE pb.user_id=? AND p.status='resolved'", (uid,)).fetchone()
    predictions = {"games": pr["g"], "wagered": pr["w"], "won": pr["p"], "net": pr["p"] - pr["w"], "biggest": pr["mx"]}

    cats = [mines, pvp, blackjack, predictions]
    overall = {
        "games": sum(c["games"] for c in cats),
        "wagered": sum(c["wagered"] for c in cats),
        "won": sum(c["won"] for c in cats),
        "net": sum(c["net"] for c in cats),
        "biggest": max(c["biggest"] for c in cats),
    }
    return {"overall": overall, "mines": mines, "pvp": pvp, "blackjack": blackjack, "predictions": predictions}


# ---------------- Síň slávy (veřejní top podporovatelé) ----------------
_hof_cache = {"at": 0.0, "data": None}


@router.get("/hall-of-fame")
def hall_of_fame(conn: sqlite3.Connection = Depends(db_dep)):
    """Veřejné žebříčky uznání (status bez gamblingu): nejvěrnější / subs / nejštědřejší / nejaktivnější. Cache 60 s."""
    import time
    _nowm = time.monotonic()
    if _hof_cache["data"] is not None and _nowm - _hof_cache["at"] < _WEEKLY_TTL:
        return _hof_cache["data"]
    def q(sql, params=()):
        return [dict(r) for r in conn.execute(sql, params)]
    loyal = q("SELECT username, avatar_url, role, created_at FROM users "
              "WHERE banned=0 AND username IS NOT NULL AND kick_username IS NOT NULL "
              "ORDER BY created_at ASC LIMIT 10")
    subs = q("SELECT username, avatar_url, role, created_at FROM users "
             "WHERE banned=0 AND is_sub=1 ORDER BY created_at ASC LIMIT 10")
    # Nejštědřejší: počet darovaných subů (z reason „… ×N") + sedláky. Řadíme dle POČTU subů
    # (× je u N první číslo v reason; „2×" z happy-hour přípony je až za ním).
    # metric = sedláky v ZÁKLADNÍM kurzu BEZ happy-hour bonusu: HH gift má v points_log 2× body
    # (reason končí „(happy 2×)"), tak ho vydělíme 2 → board ukazuje férový základ (≈ subs × základ),
    # ne nafouknutý HH součet. Body v zůstatku hráče HH bonus pochopitelně mají dál – tohle je jen board.
    import re as _re
    _gagg = {}
    for r in conn.execute(
            "SELECT u.id AS uid, u.username, u.avatar_url, u.role, l.reason, l.change "
            "FROM points_log l JOIN users u ON u.id=l.user_id "
            "WHERE l.reason LIKE 'Kick gift sub %' AND l.reason NOT LIKE '%příjemce%' AND u.banned=0"):
        g = _gagg.get(r["uid"])
        if g is None:
            g = _gagg[r["uid"]] = {"username": r["username"], "avatar_url": r["avatar_url"],
                                   "role": r["role"], "metric": 0, "subs": 0}
        _ch = r["change"] or 0
        if "happy" in (r["reason"] or "").lower():   # happy-hour 2× → odečti bonus, počítej jen základ
            _ch //= 2
        g["metric"] += _ch
        _m = _re.search(r"\d+", r["reason"] or "")
        g["subs"] += int(_m.group()) if _m else 1
    gifters = sorted(_gagg.values(), key=lambda x: (x["subs"], x["metric"]), reverse=True)[:10]
    active = q("SELECT u.username, u.avatar_url, u.role, COUNT(*) AS metric "
               "FROM points_log l JOIN users u ON u.id=l.user_id "
               "WHERE l.reason='Aktivita v chatu' AND u.banned=0 GROUP BY u.id ORDER BY metric DESC LIMIT 10")
    data = {"loyal": loyal, "subs": subs, "gifters": gifters, "active": active}
    _hof_cache.update(at=_nowm, data=data)
    return data


# ---------------- Nábor moderátorů (přihláška) ----------------
@router.get("/mod-apply/status")
def mod_apply_status(user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Je nábor otevřený + má už uživatel přihlášku? (pro stránku formuláře)."""
    is_open = get_setting(conn, "modapp_open", "1") == "1"
    row = conn.execute(
        "SELECT status FROM mod_applications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user["id"],)).fetchone()
    return {"open": is_open, "applied": bool(row), "status": row["status"] if row else None,
            "is_staff": user["role"] in ("mod", "admin", "broadcaster")}


@router.post("/mod-apply")
def mod_apply_submit(data: ModApplyIn, user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Odešle přihlášku na moderátora. Jedna čekající na uživatele."""
    if get_setting(conn, "modapp_open", "1") != "1":
        raise HTTPException(status_code=403, detail="Nábor moderátorů je právě uzavřený. 🔒")
    if user["role"] in ("mod", "admin", "broadcaster"):
        raise HTTPException(status_code=400, detail="Už jsi členem týmu. 🙂")
    if conn.execute("SELECT id FROM mod_applications WHERE user_id = ? AND status = 'pending' LIMIT 1",
                    (user["id"],)).fetchone():
        raise HTTPException(status_code=400, detail="Přihlášku už máš odeslanou a čeká na vyřízení. ⏳")
    rate_limit(f"modapply:{user['id']}", 3, 3600)   # anti-spam
    conn.execute(
        "INSERT INTO mod_applications (user_id, answers, status, created_at) VALUES (?, ?, 'pending', ?)",
        (user["id"], json.dumps(data.model_dump(), ensure_ascii=False), now_iso()))
    conn.commit()
    return {"ok": True, "message": "🛡️ Přihláška odeslána! Ozveme se ti přes zvoneček. Díky moc! 🌾"}


@router.get("/top-chatters")
def top_chatters_ep(period: str = Query("day", max_length=8),
                    conn: sqlite3.Connection = Depends(db_dep)):
    """Žebříček nejaktivnějších v chatu (veřejné). period = day | week."""
    from ..topchatter import top_chatters
    return top_chatters(conn, "week" if period == "week" else "day", 10)


@router.get("/quests")
def quests_list(user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Denní/týdenní úkoly aktuálního uživatele (postup + stav vyzvednutí)."""
    from ..quests import get_quests, QUESTS_ENABLED
    if not QUESTS_ENABLED:          # úkoly dočasně mimo provoz → prázdno (frontend kartu schová)
        return []
    return get_quests(conn, user["id"])


@router.post("/quests/claim")
def quests_claim(data: QuestClaimIn, user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvedne odměnu za splněný úkol (server ověří splnění, nevěří klientovi)."""
    from ..quests import claim_quest, QUESTS_ENABLED
    if not QUESTS_ENABLED:
        raise HTTPException(status_code=400, detail="Úkoly jsou dočasně mimo provoz. 🛠️ Zkus to prosím později.")
    try:
        return claim_quest(conn, user["id"], data.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/battlepass")
def battlepass_status(user: sqlite3.Row = Depends(require_user),
                      conn: sqlite3.Connection = Depends(db_dep)):
    """Farmářský Battle Pass aktuálního uživatele (tier, postup, odměny po tierech)."""
    from .. import battlepass
    return battlepass.status(conn, user)


@router.post("/battlepass/claim")
def battlepass_claim(data: BattlePassClaimIn, user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvedne odměnu za odemčený tier (server ověří odemčení)."""
    from .. import battlepass
    r = battlepass.claim(conn, user, data.tier, getattr(data, "premium", False))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Tento tier teď vyzvednout nejde."))
    return r


@router.get("/level-pass")
def level_pass_status(user: sqlite3.Row = Depends(require_user),
                      conn: sqlite3.Connection = Depends(db_dep)):
    """Level Pass uživatele: aktuální úroveň + milníky (10/25/50/75/100) a jejich stav."""
    from .. import levelpass
    return levelpass.status(conn, user)


@router.post("/level-pass/claim")
def level_pass_claim(data: LevelPassClaimIn, user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvedne milník Level Passu (exkluzivní kosmetika; server ověří dosaženou úroveň)."""
    from .. import levelpass
    r = levelpass.claim(conn, user, data.level)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Tenhle milník teď vyzvednout nejde."))
    return r


@router.get("/partner-links")
def partner_links_list(user: sqlite3.Row = Depends(require_user),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Zapnuté partnerské odkazy + stav (claimable/claimed dle režimu + flash okno)."""
    return partners.status_for_user(conn, user["id"])


@router.post("/partner-links/{link_id}/claim")
def partner_links_claim(link_id: int, user: sqlite3.Row = Depends(require_user),
                        conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvedne jednorázovou odměnu za proklik partnerského odkazu (1× za uživatele)."""
    try:
        return partners.claim(conn, user["id"], link_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- Kosmetika (barvy nicku / rámečky / bannery) ----------------
@router.get("/cosmetics")
def cosmetics_list(user: sqlite3.Row = Depends(require_user),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Katalog kosmetiky + stav uživatele (vlastněno / nasazeno)."""
    return cosmetics.list_for_user(conn, user)


@router.post("/cosmetics/buy")
def cosmetics_buy(data: CosmeticIn, user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Koupí kosmetiku za sedláky (nevratné, vlastníš navždy)."""
    rate_limit(f"cosbuy:{user['id']}", 10, 60)
    try:
        item = cosmetics.buy(conn, user, data.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "key": item["key"],
            "message": f"🎨 Koupeno: {item['name']}! Nasadit si to můžeš v sekci Kosmetika."}


@router.post("/cosmetics/equip")
def cosmetics_equip(data: CosmeticIn, user: sqlite3.Row = Depends(require_user),
                    conn: sqlite3.Connection = Depends(db_dep)):
    """Nasadí/sundá kosmetiku (toggle – druhý klik ji sundá). Musíš ji vlastnit."""
    try:
        return {"ok": True, **cosmetics.equip(conn, user, data.key)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _try_claim_drop(conn, user, request, raw_code):
    """Sjednocení: když je zadaný „kód" AKTIVNÍ drop, nárokuje ho (závod o kód).

    Vrátí redeem-style odpověď, nebo None když to drop není. Banner anti-bot (honeypot/
    dwell) se nepoužije (na to je drop banner) – redeem už dělá rate-limit + anticheat.
    """
    code = (raw_code or "").strip().lstrip("#").upper()
    d = conn.execute(
        "SELECT * FROM drops WHERE active = 1 AND UPPER(code) = ? ORDER BY id DESC LIMIT 1", (code,),
    ).fetchone()
    if not d:
        return None
    ip = client_ip(request)
    sig = conn.execute(
        "SELECT fp_hash FROM client_signals WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user["id"],),
    ).fetchone()
    fp = (sig["fp_hash"] if sig else "") or ""
    # (Limit „1 chyt na IP/zařízení na drop" VYPNUT na přání – i přes pole „Uplatnit kód".)
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO drop_claims (drop_id, user_id, position, ip, fp_hash, created_at) "
        "SELECT :did, :uid, (SELECT COUNT(*) FROM drop_claims WHERE drop_id = :did) + 1, :ip, :fp, :ts "
        "WHERE (SELECT COUNT(*) FROM drop_claims WHERE drop_id = :did) < :maxw "
        "AND NOT EXISTS (SELECT 1 FROM drop_claims WHERE drop_id = :did AND user_id = :uid)",
        {"did": d["id"], "uid": user["id"], "ip": ip, "fp": fp, "ts": ts, "maxw": d["max_winners"]},
    )
    if cur.rowcount == 0:
        mine = conn.execute(
            "SELECT position FROM drop_claims WHERE drop_id = ? AND user_id = ?", (d["id"], user["id"]),
        ).fetchone()
        conn.commit()
        if mine:
            raise HTTPException(status_code=400, detail=f"Tento drop už jsi chytil (pozice {mine['position']}). 🙂")
        raise HTTPException(status_code=400, detail="Tento drop je už rozebraný! 😢 Příště buď rychlejší. ⚡")
    position = conn.execute(
        "SELECT position FROM drop_claims WHERE drop_id = ? AND user_id = ?", (d["id"], user["id"]),
    ).fetchone()["position"]
    add_points(conn, user["id"], d["points"], f"Drop #{d['id']} – {position}. místo (přes kód)")
    if position >= d["max_winners"]:
        conn.execute("UPDATE drops SET active = 0, ended_at = ? WHERE id = ?", (ts, d["id"]))
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {"ok": True, "balance": fresh["points"],
            "message": f"🎁 Drop chycen! {position}. místo · +{d['points']} sedláků 🌾"}


@router.post("/redeem")
def redeem(data: RedeemIn, request: Request,
           user: sqlite3.Row = Depends(require_user),
           conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"redeem:{user['id']}", 6, 60)  # max 6 pokusů / min

    # --- ANTI-BOT: risk score blokuje vysoký combined risk ---
    check_or_block(conn, user, request, context="redeem", t0_ms=data.t0,
                   block_msg="Uplatnění kódu jsme zablokovali ochranou proti zneužití.")

    code = data.code.strip()
    # AKTIVNÍ drop má přednost (živý závod o kód) – zabere i když existuje stejnojmenný
    # starý/expirovaný redeem kód (jinak by ho ten expirovaný kód „zastínil").
    drop_res = _try_claim_drop(conn, user, request, code)
    if drop_res:
        return drop_res
    row = conn.execute(
        "SELECT * FROM redeem_codes WHERE UPPER(code) = UPPER(?)", (code,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Tento kód neplatí. 🤔")
    if row["expires_at"] and row["expires_at"] < now_iso():
        raise HTTPException(status_code=400, detail="Platnost tohoto kódu už vypršela.")
    if row["uses_count"] >= row["max_uses"]:
        raise HTTPException(status_code=400, detail="Tento kód je už vyčerpaný.")
    # Atomický claim místa: increment PŘED dalšími operacemi, podmíněný na nepřekročení limitu.
    # Chrání před souběžným dvojím uplatněním stejného single-use kódu dvěma různými uživateli.
    cur_claim = conn.execute(
        "UPDATE redeem_codes SET uses_count = uses_count + 1 WHERE id = ? AND uses_count < max_uses",
        (row["id"],))
    if cur_claim.rowcount == 0:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Tento kód je už vyčerpaný.")
    already = conn.execute(
        "SELECT 1 FROM redeem_uses WHERE code_id = ? AND user_id = ?",
        (row["id"], user["id"]),
    ).fetchone()
    if already:
        raise HTTPException(status_code=400, detail="Tento kód jsi už použil. 🙂")

    # --- ANTI-BOT: nové účty mají cap na získané body z kódů (první 24 h) ---
    if is_new_account(user) and row["points_value"] > 0:
        already_pts = new_account_redeem_pts(conn, user["id"])
        if already_pts + row["points_value"] > NEW_ACCOUNT_MAX_REDEEM_PTS:
            raise HTTPException(
                status_code=429,
                detail=f"Nové účty (mladší 24 h) mohou z kódů získat nejvýše {NEW_ACCOUNT_MAX_REDEEM_PTS} sedláků. "
                       f"Zatím máš {already_pts}.",
            )

    points_added = 0
    product_payload = None
    parts = []

    if row["points_value"] and row["points_value"] > 0:
        points_added = row["points_value"]
        add_points(conn, user["id"], points_added, f"Redeem kód {row['code']}")
        parts.append(f"+{points_added} b")

    if row["product_id"]:
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (row["product_id"],)
        ).fetchone()
        if product:
            if not role_allows(user, product):
                raise HTTPException(status_code=403,
                                    detail="Tuto odměnu uplatnit nemůžeš (je jen pro sub/VIP).")
            conn.execute(
                "INSERT INTO orders (user_id, product_id, product_name, points_spent, status, created_at) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (user["id"], product["id"], product["name"], ORDER_PENDING, now_iso()),
            )
            if product["stock"] != UNLIMITED_STOCK:
                cur_stock = conn.execute(
                    "UPDATE products SET stock = stock - 1 WHERE id = ? AND stock > 0",
                    (product["id"],))
                if cur_stock.rowcount == 0:
                    conn.rollback()
                    raise HTTPException(status_code=400, detail="Tato odměna je vyprodaná. 😔")
            product_payload = product_public(product)
            parts.append(f"odměna „{product['name']}“")

    conn.execute(
        "INSERT INTO redeem_uses (code_id, user_id, created_at) VALUES (?, ?, ?)",
        (row["id"], user["id"], now_iso()),
    )
    conn.commit()

    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True,
        "points_added": points_added,
        "product": product_payload,
        "balance": fresh["points"],
        "message": "Kód uplatněn: " + (", ".join(parts) if parts else "hotovo") + ".",
    }


# ---------------- Pasivní výdělek: sledování + přehled ----------------
@router.post("/activity/heartbeat")
def activity_heartbeat(request: Request, user: sqlite3.Row = Depends(require_user),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Tep ze živé záložky – body za sledování (PTS/min × násobič). Anti-spam přes cooldown."""
    rate_limit(f"hb:{user['id']}", 4, 50)  # max 4 heartbeaty / ~50 s (klient posílá ~1/min)
    # boti/headless/VPN se do pasivního výdělku nepočítají
    risk = check_or_block(conn, user, request, context="claim",
                          block_msg="Body za sledování blokovány (anti-bot).")
    res = economy.award_watch(conn, user)
    res["balance"] = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    res["summary"] = economy.activity_summary(conn, user)
    return res


@router.get("/activity/summary")
def activity_summary(user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Dnešní pasivní výdělek + nastavení (pro UI banner)."""
    return economy.activity_summary(conn, user)


@router.get("/profile/orders")
def my_orders(user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT o.id, o.points_spent, o.status, o.created_at, COALESCE(p.name, o.product_name) AS product_name, "
        "       p.type AS product_type FROM orders o "
        "LEFT JOIN products p ON p.id = o.product_id "
        "WHERE o.user_id = ? ORDER BY o.created_at DESC, o.id DESC",
        (user["id"],),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "product_name": r["product_name"] or "(smazaná odměna)",
            "product_type": r["product_type"],
            "points_spent": r["points_spent"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("/profile/points-log")
def my_points_log(user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    from ..econ_health import normalized_reason
    rows = conn.execute(
        "SELECT change, reason, created_at FROM points_log WHERE user_id = ? AND change != 0 "
        "ORDER BY created_at DESC, id DESC LIMIT 100",
        (user["id"],),
    ).fetchall()
    return [
        {"change": r["change"], "reason": r["reason"], "created_at": r["created_at"],
         "category": normalized_reason(r["reason"])}
        for r in rows
    ]


# ---------------- Steam trade link (na ruční výplatu skinů) ----------------
# Tvar: https://steamcommunity.com/tradeoffer/new/?partner=<číslo>&token=<token>
_TRADE_RE = re.compile(
    r"^https://steamcommunity\.com/tradeoffer/new/\?partner=\d+&token=[A-Za-z0-9_-]{6,}$"
)


@router.post("/profile/trade-url")
def set_trade_url(data: TradeUrlIn,
                  user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Uloží Steam trade odkaz přihlášeného diváka (prázdné = smazat)."""
    url = (data.url or "").strip()
    if url and not _TRADE_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail="Neplatný Steam trade odkaz. Zkopíruj ho prosím celý ze Steamu, má tvar "
                   "https://steamcommunity.com/tradeoffer/new/?partner=…&token=…",
        )
    conn.execute("UPDATE users SET steam_trade_url = ? WHERE id = ?",
                 (url or None, user["id"]))
    conn.commit()
    return {"steam_trade_url": url or None}


_FAV_GAMES = {"", "Mines", "Kolo štěstí", "Piškvorky", "Duely", "Blackjack", "Predikce", "Tomboly"}


@router.post("/profile/bio")
def set_bio(data: ProfileBioIn, user: sqlite3.Row = Depends(require_user),
            conn: sqlite3.Connection = Depends(db_dep)):
    """Nastaví bio + vypíchnutou oblíbenou hru na vlastním profilu (showcase)."""
    bio = " ".join((data.bio or "").split())[:160]       # ořež + sjednoť whitespace
    fav = (data.fav_game or "").strip()
    if fav not in _FAV_GAMES:
        fav = ""
    conn.execute("UPDATE users SET bio = ?, fav_game = ? WHERE id = ?",
                 (bio or None, fav or None, user["id"]))
    conn.commit()
    return {"bio": bio, "fav_game": fav}


# ---------------- 🔥 Prestige (spal sedláky za permanentní status; anti-inflace sink) ----------------
PRESTIGE_BASE = 100000   # cena 1. levelu; každý další = BASE × (level+1) → eskaluje
PRESTIGE_MAX = 50


def _prestige_cost(level: int) -> int:
    return PRESTIGE_BASE * (level + 1)


def _user_prestige(u) -> int:
    try:
        return (u["prestige"] if "prestige" in u.keys() else 0) or 0
    except (KeyError, IndexError, TypeError):
        return 0


@router.get("/prestige")
def prestige_status(user: sqlite3.Row = Depends(require_user)):
    p = _user_prestige(user)
    return {"prestige": p, "next_cost": _prestige_cost(p) if p < PRESTIGE_MAX else None,
            "max": PRESTIGE_MAX, "balance": user["points"], "base": PRESTIGE_BASE}


@router.post("/prestige/buy")
def prestige_buy(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    """Spálí sedláky za +1 prestige (NEvratné). Body opravdu zmizí z oběhu (sink)."""
    p = _user_prestige(user)
    if p >= PRESTIGE_MAX:
        raise HTTPException(status_code=400, detail="Máš už maximální prestige. 👑")
    cost = _prestige_cost(p)
    if not try_debit(conn, user["id"], cost, f"Prestige {p + 1} – spáleno 🔥"):
        raise HTTPException(status_code=400, detail=f"Nemáš dost sedláků. Prestige {p + 1} stojí {cost}.")
    conn.execute("UPDATE users SET prestige = prestige + 1 WHERE id = ?", (user["id"],))
    conn.commit()
    fresh = conn.execute("SELECT points, prestige FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"prestige": fresh["prestige"], "balance": fresh["points"], "spent": cost,
            "next_cost": _prestige_cost(fresh["prestige"]) if fresh["prestige"] < PRESTIGE_MAX else None}


# ---------------- 🛡️ Denní limit sázek (responsible gaming) ----------------
@router.get("/wager-limit")
def wager_limit_status(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    from ..db import local_date
    today = local_date()
    r = conn.execute(
        "SELECT wager_limit, wager_limit_pending, wagered_today, wager_day FROM users WHERE id=?",
        (user["id"],)).fetchone()
    same_day = r and r["wager_day"] == today
    eff_limit = (r["wager_limit"] if r else None)
    if r and not same_day and r["wager_limit_pending"] is not None:
        eff_limit = r["wager_limit_pending"]          # zítra se aplikuje odložené
    try:
        global_limit = int(get_setting(conn, "eco_wager_cap", "75000") or "0")
    except (TypeError, ValueError):
        global_limit = 75000
    if user["role"] == ROLE_ADMIN:
        global_limit = 0
    if global_limit > 0:
        eff_limit = min(eff_limit, global_limit) if eff_limit and eff_limit > 0 else global_limit
    wagered = (r["wagered_today"] if (r and same_day) else 0) or 0
    return {
        "limit": eff_limit or 0,
        "wagered_today": wagered,
        "remaining": (max(0, eff_limit - wagered) if eff_limit and eff_limit > 0 else None),
        "pending": (r["wager_limit_pending"] if r else None),
        "balance": user["points"],
    }


@router.post("/wager-limit")
def set_wager_limit(data: WagerLimitIn, user: sqlite3.Row = Depends(require_user),
                    conn: sqlite3.Connection = Depends(db_dep)):
    """Nastaví denní limit sázek. SNÍŽENÍ/přidání = HNED, ZVÝŠENÍ/odebrání = až ZÍTRA
    (responsible gaming – nejde si limit v tiltu okamžitě navýšit). 0 = bez limitu."""
    cur = conn.execute("SELECT wager_limit FROM users WHERE id=?", (user["id"],)).fetchone()["wager_limit"]
    inf = float("inf")
    cur_eff = cur if (cur and cur > 0) else inf
    new = max(0, data.limit)
    new_eff = new if new > 0 else inf
    if new_eff <= cur_eff:                              # stejně/víc restriktivní → hned
        conn.execute("UPDATE users SET wager_limit=?, wager_limit_pending=NULL WHERE id=?",
                     (new or None, user["id"]))
        conn.commit()
        return {"applied": "now", "limit": new, "pending": None}
    conn.execute("UPDATE users SET wager_limit_pending=? WHERE id=?", (new, user["id"]))   # zítra
    conn.commit()
    return {"applied": "tomorrow", "limit": cur or 0, "pending": new}


# ---------------- Exchange: poslání sedláků kamarádovi ----------------
# Darování sedláků – přepínač. False = MIMO PROVOZ (neukáže se a neprojde). Zpět nastav na True.
GIFT_ENABLED = True


def _shared_identity(conn: sqlite3.Connection, uid1: int, uid2: int) -> bool:
    """Sdílí dva účty IP nebo otisk zařízení? (anti-alt – brání farmení přes vlastní účty)."""
    def ips(uid):
        return {r["ip"] for r in conn.execute(
            "SELECT DISTINCT ip FROM login_events WHERE user_id=? AND ip IS NOT NULL AND ip!=''", (uid,))}

    def fps(uid):
        return {r["fp_hash"] for r in conn.execute(
            "SELECT DISTINCT fp_hash FROM client_signals WHERE user_id=? AND fp_hash IS NOT NULL", (uid,))}
    return bool(ips(uid1) & ips(uid2)) or bool(fps(uid1) & fps(uid2))


@router.post("/exchange/gift")
def gift_points(data: GiftIn, request: Request,
                user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Pošle sedláky jinému divákovi. Nevratné. Anti-alt: ne na stejnou IP/zařízení."""
    if not GIFT_ENABLED:
        raise HTTPException(status_code=403, detail="Darování sedláků je dočasně mimo provoz. 🔧 Zkus to prosím později.")
    rate_limit(f"gift:{user['id']}", 5, 60)
    key = (data.username or "").strip().lstrip("@").lower()
    if not key:
        raise HTTPException(status_code=400, detail="Zadej prosím příjemce. 🙂")
    rcp = conn.execute(
        "SELECT * FROM users WHERE LOWER(kick_username) = ? OR LOWER(username) = ? "
        "ORDER BY (kick_username IS NOT NULL) DESC LIMIT 1", (key, key),
    ).fetchone()
    if not rcp or rcp["banned"]:
        raise HTTPException(status_code=400, detail="Příjemce nebyl nalezen nebo ho nelze obdarovat.")
    if rcp["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="Sobě poslat sedláky nemůžeš. 😄")
    # anti-funnel: nový účet nesmí hned posílat dary. Klasický trik je založit alt na čisté
    # IP/zařízení a poslat body na hlavní účet DŘÍV, než se stihne propojit otisk – proto
    # darování pustíme až po pár dnech od založení (admin výjimka). Doplňuje _shared_identity níž.
    if user["role"] != ROLE_ADMIN and is_new_account(user, hours=GIFT_MIN_AGE_HOURS):
        raise HTTPException(
            status_code=403,
            detail=f"Darovat můžeš až {GIFT_MIN_AGE_HOURS} h po založení účtu "
                   f"(ochrana proti farmění bodů přes nové účty). 🛡️")
    # anti-farma: nelze posílat účtu ze stejné IP/zařízení (admin výjimka)
    if user["role"] != ROLE_ADMIN and rcp["role"] != ROLE_ADMIN and _shared_identity(conn, user["id"], rcp["id"]):
        raise HTTPException(status_code=403,
                            detail="Účtu ze stejné sítě nebo zařízení poslat nelze (ochrana proti farmení). 🛡️")
    # Dar = ŽÁDOST, kterou schvaluje admin. Odesílateli se body HNED zablokují (escrow), aby je
    # mezitím nemohl utratit dvakrát; admin pak dar POVOLÍ (přesun příjemci) nebo ZAMÍTNE (vrácení).
    # Escrow řádek v points_log má NEUTRÁLNÍ důvod – nepasuje na 'Dar pro %', takže funnel detektor
    # ani přehled darů ho nepočítají, dokud admin nepovolí (tehdy se přejmenuje na kanonický tvar).
    amt = data.amount
    cur = conn.execute(
        "UPDATE users SET points = points - ? WHERE id = ? AND points >= ?", (amt, user["id"], amt))
    if cur.rowcount == 0:
        raise HTTPException(status_code=400, detail=f"Nemáš dost sedláků (máš {user['points']}).")
    log_cur = conn.execute(
        "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
        (user["id"], -amt, f"Dar → {rcp['username']} (čeká na schválení) 🎁", now_iso()))
    note = (data.note or "").strip()
    conn.execute(
        "INSERT INTO gift_requests (from_user_id, to_user_id, amount, status, note, escrow_log_id, created_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
        (user["id"], rcp["id"], amt, note, log_cur.lastrowid, now_iso()))
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": fresh, "recipient": rcp["username"], "amount": amt, "pending": True,
            "message": f"🎁 Žádost o dar {amt} sedláků pro {rcp['username']} čeká na schválení adminem. "
                       f"Body máš zatím zablokované – pokud admin žádost zamítne, vrátí se ti zpět."}


# ---------------- Notifikace (zvoneček v hlavičce) ----------------
@router.get("/notifications")
def notifications_list(user: sqlite3.Row = Depends(require_user),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Posledních 30 notifikací uživatele + počet nepřečtených."""
    rows = conn.execute(
        "SELECT id, icon, title, body, link, read, created_at FROM notifications "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 30", (user["id"],)).fetchall()
    unread = conn.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read = 0",
        (user["id"],)).fetchone()["c"]
    return {"items": [dict(r) for r in rows], "unread": unread}


@router.get("/notifications/unread")
def notifications_unread(user: sqlite3.Row = Depends(require_user),
                         conn: sqlite3.Connection = Depends(db_dep)):
    """Jen počet nepřečtených – levný poll pro badge."""
    c = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read = 0",
                     (user["id"],)).fetchone()["c"]
    return {"count": c}


@router.post("/notifications/read")
def notifications_mark_read(user: sqlite3.Row = Depends(require_user),
                            conn: sqlite3.Connection = Depends(db_dep)):
    """Označí všechny notifikace uživatele jako přečtené."""
    conn.execute("UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0", (user["id"],))
    conn.commit()
    return {"ok": True}


# ---------------- Denní bonus – 7denní streak ----------------
DAILY_LADDER = [10, 20, 30, 40, 50, 75, 200]  # PTS za den 1..7
DAILY_COOLDOWN_H = 20    # jak často lze vyzvednout
DAILY_RESET_H = 48       # po výpadku se cyklus resetuje


def _daily_state(user: sqlite3.Row, now: datetime):
    """(streak, can_claim, next_in_s) s ohledem na reset cyklu."""
    last = user["last_daily"]; streak = user["daily_streak"] or 0
    can, next_in = True, 0
    if last:
        elapsed = (now - datetime.fromisoformat(last)).total_seconds()
        if elapsed < DAILY_COOLDOWN_H * 3600:
            can, next_in = False, int(DAILY_COOLDOWN_H * 3600 - elapsed)
        elif elapsed > DAILY_RESET_H * 3600:
            streak = 0  # vynechal den → reset týdenního cyklu
    return streak, can, next_in


@router.get("/daily/status")
def daily_status(user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    now = datetime.now(timezone.utc)
    streak, can, next_in = _daily_state(user, now)
    done = streak % 7  # vybráno v aktuálním týdnu
    rank = user_rank(conn, user["points"], user["username"])
    return {
        "ladder": DAILY_LADDER,
        "mult": tier_for_rank(rank)[1],
        "rank": rank,
        "done_count": done,
        "day": done + 1,           # DEN x/7
        "can_claim": can,
        "reward_now": DAILY_LADDER[done],
        "next_in_seconds": next_in,
        "streak_total": user["daily_streak"] or 0,
    }


@router.post("/daily/claim")
def daily_claim(user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    now = datetime.now(timezone.utc)
    streak, can, next_in = _daily_state(user, now)
    if not can:
        hrs = round(next_in / 3600, 1)
        raise HTTPException(status_code=400, detail=f"Denní bonus už sis dnes vyzvedl. Vrať se za {hrs} h. ⏳")
    idx = streak % 7
    mult = tier_for_rank(user_rank(conn, user["points"], user["username"]))[1]
    reward = DAILY_LADDER[idx] * mult
    # Atomický claim: přepni last_daily JEN když má pořád hodnotu, kterou jsme načetli (optimistic lock).
    # Při souběhu (16 requestů) přepne řádek jen první z nich – zbytek má WHERE last_daily=prev nesplněno
    # (rowcount==0) → odmítnut, takže odměnu i streak připíše právě jeden request, ne všechny.
    prev = user["last_daily"]
    if prev is None:
        claimed = conn.execute(
            "UPDATE users SET last_daily = ?, daily_streak = ? WHERE id = ? AND last_daily IS NULL",
            (now.isoformat(), streak + 1, user["id"]))
    else:
        claimed = conn.execute(
            "UPDATE users SET last_daily = ?, daily_streak = ? WHERE id = ? AND last_daily = ?",
            (now.isoformat(), streak + 1, user["id"], prev))
    if claimed.rowcount == 0:
        conn.commit()
        raise HTTPException(status_code=400, detail="Denní bonus už sis dnes vyzvedl. ⏳")
    add_points(conn, user["id"], reward, f"Denní streak – den {idx + 1} (×{mult} liga)")
    from ..logincal import mark as _mark_cal
    _mark_cal(conn, user["id"])     # login kalendář: označ dnešní den jako aktivní
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True, "reward": reward, "mult": mult, "day": idx + 1, "streak": streak + 1,
        "balance": fresh["points"], "message": f"🔥 Den {idx + 1}/7 — získáváš +{reward} sedláků (×{mult} liga)!",
    }


@router.get("/login-calendar")
def login_calendar(user: sqlite3.Row = Depends(require_user),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Login kalendář: aktivní dny v aktuálním měsíci + milníkové bonusy."""
    from ..logincal import status
    return status(conn, user)


@router.post("/login-calendar/claim")
def login_calendar_claim(data: LoginCalClaimIn, user: sqlite3.Row = Depends(require_user),
                         conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvedne milníkový bonus za X aktivních dní v měsíci."""
    from ..logincal import claim
    r = claim(conn, user, data.milestone)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Tento milník teď vyzvednout nejde."))
    return r


import os as _os
GARDEN_OFF = _os.environ.get("WEBOS_GARDEN_OFF", "0") == "1"   # zahrádka mimo provoz (redesign); env ve fly.toml. Lokálně/testy = zapnutá.


def _garden_guard():
    """Zahrádka dočasně vypnutá → 503. Data (plodiny/dekorace) zůstávají, jen se nedá hrát."""
    if GARDEN_OFF:
        raise HTTPException(status_code=503, detail="Zahrádka je teď mimo provoz – vylepšujeme ji. Brzy bude zpátky! 🚧🌱")


@router.get("/garden")
def garden_status(user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Zahrádka: stav záhonů (prázdný/roste/hotovo) + dostupné plodiny."""
    _garden_guard()
    from .. import garden
    return garden.status(conn, user)


@router.post("/garden/plant")
def garden_plant(data: GardenPlantIn, user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    """Zasadí plodinu na záhon (zaplatí sazbu)."""
    _garden_guard()
    from .. import garden
    r = garden.plant(conn, user, data.plot, data.crop)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Zasadit se to teď nepodařilo."))
    return r


@router.post("/garden/harvest")
def garden_harvest(data: GardenHarvestIn, user: sqlite3.Row = Depends(require_user),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Sklidí dorostlý záhon (odměna)."""
    _garden_guard()
    from .. import garden
    r = garden.harvest(conn, user, data.plot)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Sklidit se to teď nepodařilo."))
    return r


@router.post("/garden/harvest-all")
def garden_harvest_all(user: sqlite3.Row = Depends(require_user),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Sklidí VŠECHNY dozrálé záhony naráz."""
    _garden_guard()
    from .. import garden
    return garden.harvest_all(conn, user)


@router.post("/garden/plant-all")
def garden_plant_all(data: GardenPlantAllIn, user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Zasadí plodinu na VŠECHNY prázdné záhony."""
    _garden_guard()
    from .. import garden
    r = garden.plant_all(conn, user, data.crop)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Zasadit se to teď nepodařilo."))
    return r


@router.post("/garden/rescue")
def garden_rescue(data: GardenHarvestIn, user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Zaplať záchranu před chrobáky na záhonu (plná sklizeň místo poloviční)."""
    _garden_guard()
    from .. import garden
    r = garden.rescue(conn, user, data.plot)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Záchrana se teď nepodařila."))
    return r


@router.get("/garden/decor")
def garden_decor(user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    """Dekorace zahrádky: katalog + co hráč vlastní."""
    _garden_guard()
    from .. import garden
    return garden.decor_status(conn, user)


_garden_lb_cache = {"at": 0.0, "data": None}


@router.get("/garden/leaderboard")
def garden_leaderboard(conn: sqlite3.Connection = Depends(db_dep)):
    """Top zahradníci dle sklizených sedláků (cache 60 s – scan points_log je drahý)."""
    import time
    if _garden_lb_cache["data"] is not None and time.time() - _garden_lb_cache["at"] < 60:
        return _garden_lb_cache["data"]
    from .. import garden
    data = garden.leaderboard(conn, 10)
    _garden_lb_cache.update(at=time.time(), data=data)
    return data


@router.post("/garden/decor/buy")
def garden_decor_buy(data: DecorBuyIn, user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Koupí dekoraci (cosmetic sink, vlastní se navždy)."""
    _garden_guard()
    from .. import garden
    r = garden.buy_decor(conn, user, data.key)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Koupit se to teď nepodařilo."))
    return r


# ---------------- Kolo štěstí (denní spin) ----------------
# Políčka kola: (sedláci, váha). Pořadí v seznamu = pořadí na kole (záměrně střídá
# velké/malé). Váhy = relativní šance; součet 100 → rovnou %. Malé časté, jackpot vzácný.
WHEEL_SEGMENTS = [
    (50, 24), (1500, 3), (100, 18), (350, 9),
    (25, 28), (3000, 1), (200, 12), (750, 5),
]
WHEEL_COOLDOWN_H = 20    # 1 zatočení / ~den (stejně jako denní bonus)
_WHEEL_JACKPOT = max(a for a, _ in WHEEL_SEGMENTS)


def _wheel_state(user: sqlite3.Row, now: datetime):
    """(can_spin, next_in_s) – 1× za WHEEL_COOLDOWN_H hodin."""
    last = user["last_wheel"]
    if last:
        elapsed = (now - datetime.fromisoformat(last)).total_seconds()
        if elapsed < WHEEL_COOLDOWN_H * 3600:
            return False, int(WHEEL_COOLDOWN_H * 3600 - elapsed)
    return True, 0


@router.get("/wheel/status")
def wheel_status(user: sqlite3.Row = Depends(require_user)):
    can, next_in = _wheel_state(user, datetime.now(timezone.utc))
    return {
        "segments": [a for a, _ in WHEEL_SEGMENTS],   # částky v pořadí na kole
        "jackpot": _WHEEL_JACKPOT,
        "can_spin": can,
        "next_in_seconds": next_in,
        "cooldown_h": WHEEL_COOLDOWN_H,
    }


def _fair_ensure(conn, uid):
    """Vrátí (server_seed, server_hash, client_seed, nonce). Líně inicializuje (1× per user)."""
    row = conn.execute(
        "SELECT fair_server_seed, fair_server_hash, fair_client_seed, fair_nonce FROM users WHERE id = ?",
        (uid,)).fetchone()
    if row["fair_server_seed"]:
        return row["fair_server_seed"], row["fair_server_hash"], row["fair_client_seed"], row["fair_nonce"] or 0
    ss = fairness.new_server_seed()
    conn.execute(
        "UPDATE users SET fair_server_seed = ?, fair_server_hash = ?, fair_client_seed = ?, fair_nonce = 0 "
        "WHERE id = ? AND fair_server_seed IS NULL",
        (ss, fairness.seed_hash(ss), fairness.new_client_seed(), uid))
    conn.commit()
    row = conn.execute(
        "SELECT fair_server_seed, fair_server_hash, fair_client_seed, fair_nonce FROM users WHERE id = ?",
        (uid,)).fetchone()
    return row["fair_server_seed"], row["fair_server_hash"], row["fair_client_seed"], row["fair_nonce"] or 0


@router.post("/wheel/spin")
def wheel_spin(user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    now = datetime.now(timezone.utc)
    # Atomicky „zaberu" dnešní spin – brání dvojímu zatočení i při souběhu requestů
    # (stejný princip jako atomický claim u sledování streamu).
    threshold = (now - timedelta(hours=WHEEL_COOLDOWN_H)).isoformat()
    cur = conn.execute(
        "UPDATE users SET last_wheel = ? WHERE id = ? AND (last_wheel IS NULL OR last_wheel < ?)",
        (now.isoformat(), user["id"], threshold),
    )
    if cur.rowcount == 0:                       # už dnes točil (nebo souběžný request předběhl)
        conn.commit()
        _, next_in = _wheel_state(user, now)
        hrs = round((next_in or WHEEL_COOLDOWN_H * 3600) / 3600, 1)
        raise HTTPException(status_code=400, detail=f"Dnes už jsi točil. 🎡 Vrať se za {hrs} h. ⏳")
    # Výsledek = PROVABLY FAIR (commit-reveal): server ho nevybírá náhodně za běhu, počítá ho
    # z předem zveřejněného server seedu + client seedu + nonce → hráč si ověří, že to nebylo
    # rigged. Stejné šance jako dřív (váhy se nemění). Viz /fair.
    ss, sh, cs, nonce = _fair_ensure(conn, user["id"])
    idx = fairness.weighted_index(ss, cs, nonce, [w for _, w in WHEEL_SEGMENTS])
    conn.execute(
        "INSERT INTO fair_log (user_id, game, server_hash, client_seed, nonce, result, created_at) "
        "VALUES (?,?,?,?,?,?,?)", (user["id"], "wheel", sh, cs, nonce, idx, now.isoformat()))
    conn.execute("UPDATE users SET fair_nonce = fair_nonce + 1 WHERE id = ?", (user["id"],))
    amount = WHEEL_SEGMENTS[idx][0]
    jackpot = amount == _WHEEL_JACKPOT
    add_points(conn, user["id"], amount, "Kolo štěstí 🎡" + (" – JACKPOT! 🎰" if jackpot else ""))
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True,
        "index": idx,
        "amount": amount,
        "jackpot": jackpot,
        "balance": fresh["points"],
        "fair": {"server_hash": sh, "client_seed": cs, "nonce": nonce},
        "message": (f"🎰 JACKPOT! +{amount} sedláků!" if jackpot
                    else f"🎡 Padlo ti +{amount} sedláků!"),
    }


@router.get("/fair/me")
def fair_me(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    """Provably-fair stav: aktuální commit (hash), client seed, nonce + posledních 20 her k ověření."""
    _ss, sh, cs, nonce = _fair_ensure(conn, user["id"])
    recent = [dict(r) for r in conn.execute(
        "SELECT game, server_hash, client_seed, nonce, result, created_at FROM fair_log "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user["id"],))]
    return {"server_hash": sh, "client_seed": cs, "nonce": nonce,
            "wheel_weights": [w for _, w in WHEEL_SEGMENTS],
            "wheel_amounts": [a for a, _ in WHEEL_SEGMENTS], "recent": recent}


@router.post("/fair/rotate")
def fair_rotate(data: FairSeedIn, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Změní seed: ODHALÍ starý server seed (na ověření minulých her) + nasadí nový commit
    a nový/zadaný client seed, nonce=0. Klasický commit-reveal cyklus."""
    old_ss, old_sh, _cs, _n = _fair_ensure(conn, user["id"])
    new_ss = fairness.new_server_seed()
    new_cs = (data.client_seed or "").strip()[:64] or fairness.new_client_seed()
    conn.execute(
        "UPDATE users SET fair_server_seed = ?, fair_server_hash = ?, fair_client_seed = ?, fair_nonce = 0 WHERE id = ?",
        (new_ss, fairness.seed_hash(new_ss), new_cs, user["id"]))
    conn.commit()
    return {"revealed_server_seed": old_ss, "revealed_server_hash": old_sh,
            "new_server_hash": fairness.seed_hash(new_ss), "client_seed": new_cs, "nonce": 0}
