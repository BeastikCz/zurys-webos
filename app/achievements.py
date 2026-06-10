"""Achievementy / odznaky.

Scanner daemon (jako digest/backup) periodicky projede staty uživatelů a udělí
odznaky, na které mají nárok. DECOUPLED – žádné zásahy do herních endpointů,
takže nízké riziko. Odznak se udělí jednou; u stupňovitých se drží nejvyšší
dosažený tier. Při prvním běhu se odznaky doplní zpětně (backfill, bez spamu).
"""
import json
import threading
import time
import traceback

from .db import get_conn, now_iso

CHECK_INTERVAL_SEC = 600   # sken každých 10 min – odznaky nejsou time-critical

# Katalog odznaků. `stat` = klíč statu (viz _collect_stats), `tiers` = prahy vzestupně.
# Dosažený tier = počet splněných prahů (3 dropy z [1] = tier 1; 60 z [10,50,100] = tier 2).
BADGES = [
    {"key": "first_drop",  "emoji": "🎯", "name": "První krok",  "desc": "Chytni svůj první drop",                    "stat": "drops",    "tiers": [1]},
    {"key": "drop_hunter", "emoji": "🏹", "name": "Lovec dropů",  "desc": "Nachytej 10 / 50 / 100 dropů",              "stat": "drops",    "tiers": [10, 50, 100]},
    {"key": "duelist",     "emoji": "⚔️", "name": "Duelista",     "desc": "Vyhraj 10 / 50 / 100 PvP (duely + piškvorky)", "stat": "pvp_won", "tiers": [10, 50, 100]},
    {"key": "gambler",     "emoji": "🎰", "name": "Gambler",      "desc": "Podej 20 / 50 / 100 sázek v predikcích",    "stat": "bets",     "tiers": [20, 50, 100]},
    {"key": "loyal",       "emoji": "🔥", "name": "Věrný divák",  "desc": "Denní série 7 / 30 / 100 dní",              "stat": "streak",   "tiers": [7, 30, 100]},
    {"key": "millionaire", "emoji": "💎", "name": "Milionář",     "desc": "Vydělej celkem 1 000 000 sedláků",          "stat": "earned",   "tiers": [1_000_000]},
    {"key": "rich",        "emoji": "🧀", "name": "Boháč",        "desc": "Měj 100 000 sedláků na účtu naráz",         "stat": "balance",  "tiers": [100_000]},
    {"key": "lucky",       "emoji": "🎟️", "name": "Šťastlivec",   "desc": "Vyhraj v tombole",                          "stat": "raffle",   "tiers": [1]},
    {"key": "champion",    "emoji": "🏆", "name": "Šampion",      "desc": "Buď #1 na žebříčku",                        "stat": "is_rank1", "tiers": [1]},
    {"key": "unlucky",     "emoji": "🃏", "name": "Smolař",       "desc": "Prohraj 10 PvP (klobouk dolů 🎩)",          "stat": "pvp_lost", "tiers": [10]},
    {"key": "sub",         "emoji": "💜", "name": "Subscriber",   "desc": "Aktivní sub kanálu",                        "stat": "is_sub",   "tiers": [1]},
    {"key": "vip",         "emoji": "👑", "name": "VIP",          "desc": "VIP status",                                "stat": "is_vip",   "tiers": [1]},
    {"key": "og",          "emoji": "🌟", "name": "OG člen",      "desc": "Patří k OG komunitě",                       "stat": "is_og",    "tiers": [1]},
]

_STAT_KEYS = ("drops", "pvp_won", "pvp_lost", "bets", "streak", "earned",
              "balance", "raffle", "is_rank1", "is_sub", "is_vip", "is_og")


def _earned_tier(tiers, value) -> int:
    """Počet splněných prahů (= dosažený tier). value < nejnižší práh → 0."""
    t = 0
    for thr in tiers:
        if value >= thr:
            t += 1
    return t


def _collect_stats(conn) -> dict:
    """user_id -> dict statů. Agregované dotazy (ne per-user), ať to škáluje."""
    stats: dict = {}

    def ensure(uid):
        if uid not in stats:
            stats[uid] = {k: 0 for k in _STAT_KEYS}
        return stats[uid]

    # users: balance, streak, flagy, rank (#1 = nejvíc bodů, řazení jako leaderboard)
    rows = conn.execute(
        "SELECT id, points, daily_streak, is_sub, is_vip, is_og "
        "FROM users ORDER BY points DESC, username ASC"
    ).fetchall()
    for i, r in enumerate(rows):
        s = ensure(r["id"])
        s["balance"] = r["points"] or 0
        s["streak"] = r["daily_streak"] or 0
        s["is_sub"] = 1 if r["is_sub"] else 0
        s["is_vip"] = 1 if r["is_vip"] else 0
        s["is_og"] = 1 if r["is_og"] else 0
        s["is_rank1"] = 1 if (i == 0 and (r["points"] or 0) > 0) else 0

    for r in conn.execute("SELECT user_id uid, COUNT(*) c FROM drop_claims GROUP BY user_id"):
        ensure(r["uid"])["drops"] = r["c"]

    # PvP výhry/prohry: duely (coinflip/dice/rps) + piškvorky (games)
    for tbl in ("duels", "games"):
        for r in conn.execute(
            f"SELECT CASE WHEN winner=1 THEN p1_id ELSE p2_id END uid, COUNT(*) c "
            f"FROM {tbl} WHERE status='finished' AND winner IN (1,2) GROUP BY uid"):
            if r["uid"] is not None:
                ensure(r["uid"])["pvp_won"] += r["c"]
        for r in conn.execute(
            f"SELECT CASE WHEN winner=1 THEN p2_id ELSE p1_id END uid, COUNT(*) c "
            f"FROM {tbl} WHERE status='finished' AND winner IN (1,2) AND p2_id IS NOT NULL GROUP BY uid"):
            if r["uid"] is not None:
                ensure(r["uid"])["pvp_lost"] += r["c"]

    for r in conn.execute("SELECT user_id uid, COUNT(*) c FROM prediction_bets GROUP BY user_id"):
        ensure(r["uid"])["bets"] = r["c"]
    for r in conn.execute("SELECT user_id uid, COALESCE(SUM(change),0) c FROM points_log WHERE change>0 GROUP BY user_id"):
        ensure(r["uid"])["earned"] = r["c"]
    for r in conn.execute("SELECT user_id uid, COUNT(*) c FROM raffle_winners GROUP BY user_id"):
        ensure(r["uid"])["raffle"] = r["c"]
    return stats


def scan_and_award(conn) -> int:
    """Projde staty a udělí nové/vyšší odznaky. Vrátí počet nově udělených/povýšených."""
    stats = _collect_stats(conn)
    existing = {(r["user_id"], r["badge_key"]): r["tier"]
                for r in conn.execute("SELECT user_id, badge_key, tier FROM user_badges")}
    ts = now_iso()
    awarded = 0
    for uid, s in stats.items():
        for b in BADGES:
            tier = _earned_tier(b["tiers"], s.get(b["stat"], 0))
            if tier <= 0:
                continue
            if tier > existing.get((uid, b["key"]), 0):
                conn.execute(
                    "INSERT INTO user_badges (user_id, badge_key, tier, awarded_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(user_id, badge_key) DO UPDATE SET tier=excluded.tier, awarded_at=excluded.awarded_at",
                    (uid, b["key"], tier, ts))
                awarded += 1
    if awarded:
        conn.commit()
    return awarded


# Pořadí lig podle prestiže (pro detekci POSTUPU). "" = mimo TOP 100.
_LEAGUE_ORDER = {"": 0, "bronze": 1, "silver": 2, "gold": 3, "elite": 4, "unreal": 5}
_LEAGUE_LABEL = {"bronze": "Bronze", "silver": "Silver", "gold": "Gold", "elite": "Elite", "unreal": "UNREAL"}
_SHOUTOUT_MIN = _LEAGUE_ORDER["gold"]   # bot oznámí v chatu jen postupy do Gold a výš
OVERTAKE_NOTIFY_MAX_RANK = 100          # „přeskočil tě" jen pro lidi v TOP 100 (kolem lig)


def scan_rankups(conn) -> int:
    """Detekuje (a) postup do VYŠŠÍ ligy → konfety (pending_rankup) + bot shoutout
    (gold+), a (b) přeskočení na žebříčku → hláška „přeskočil tě" pro lidi v TOP N.
    Obojí jen NOVÝ stav (žádný spam z churnu). Vrátí počet bot oznámení."""
    from .deps import tier_for_rank
    rows = conn.execute(
        "SELECT id, username, points, last_league, last_rank FROM users ORDER BY points DESC, username ASC"
    ).fetchall()
    names = [r["username"] for r in rows]          # index = rank-1 (kdo sedí na které pozici)
    announces = []
    changed = False
    for i, r in enumerate(rows):
        rank, uid = i + 1, r["id"]

        # (a) liga – postup
        league, _ = tier_for_rank(rank)
        last_lg = r["last_league"]
        if last_lg is None:
            conn.execute("UPDATE users SET last_league=? WHERE id=?", (league, uid)); changed = True
        elif _LEAGUE_ORDER.get(league, 0) > _LEAGUE_ORDER.get(last_lg, 0):
            conn.execute("UPDATE users SET last_league=?, pending_rankup=? WHERE id=?", (league, league, uid))
            changed = True
            if _LEAGUE_ORDER.get(league, 0) >= _SHOUTOUT_MIN:
                announces.append((r["username"], league))

        # (b) pozice – přeskočení (jen TOP N, ať to nespamuje tisíce lidí dole)
        last_rk = r["last_rank"]
        if last_rk is None:
            conn.execute("UPDATE users SET last_rank=? WHERE id=?", (rank, uid)); changed = True
        elif rank > last_rk and last_rk <= OVERTAKE_NOTIFY_MAX_RANK and (last_rk - 1) < len(names):
            payload = json.dumps({"by": names[last_rk - 1], "rank": rank})
            conn.execute("UPDATE users SET last_rank=?, pending_overtake=? WHERE id=?", (rank, payload, uid))
            changed = True
        elif rank < last_rk:                        # zlepšení → smaž starou neukázanou hlášku o pádu
            conn.execute("UPDATE users SET last_rank=?, pending_overtake=NULL WHERE id=?", (rank, uid)); changed = True
        elif rank > last_rk:                        # pád mimo TOP N → jen aktualizuj pozici
            conn.execute("UPDATE users SET last_rank=? WHERE id=?", (rank, uid)); changed = True

    if changed:
        conn.commit()
    if announces:
        from . import kickbot
        for uname, league in announces[:5]:        # max 5/cyklus, ať to nespamuje chat
            try:
                kickbot.send_message(
                    conn, f"🏆 {uname} právě postoupil do ligy {_LEAGUE_LABEL.get(league, league)}! Gratulace! 🎉",
                    kind="system")
            except Exception:
                traceback.print_exc()
    return len(announces)


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            try:
                scan_and_award(conn)
                scan_rankups(conn)
                from . import topchatter
                topchatter.maybe_payout(conn)        # 1× denně výplata TOP 3 chatterů
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_achievements_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-achievements", daemon=True)
    _thread.start()
