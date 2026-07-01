"""Simulace: jak se zmeni weekly + season board po vylouceni 'zero' (gambling/admin/transfer).
Replikuje app.deps.classify_xp PRESNE (kopie _XP_ZERO_KW). Read-only.
flyctl ssh console -a zurys-shop -C "python3 -" < scripts/board_sim.py
"""
import sqlite3
from datetime import datetime, timezone, timedelta

# --- VERBATIM kopie z app/deps.py (classify_xp) ---
_XP_ZERO_KW = ("battle pass", "mines", "blackjack", "coinflip", "duel", "predikce", "piškvor",
               "kostky", "stůl – win", "sázka", "výhra v", "zrušen", "zrušená", "vrácen", "vráceno",
               "vypršel", "vypršelá", "remíza", "storno", "refund", "výzv", "hra #", "dar od",
               "dar pro", "dar →", "úprava adminem", "chat cíl", "sub cíl", "komunitní", "botrix")

_cache = {}
def is_zero(reason):
    r = reason
    v = _cache.get(r)
    if v is not None:
        return v
    rl = (r or "").lower()
    if "kick gift sub" in rl and "příjemce" not in rl: out = False
    elif ("kick sub" in rl or "kick resub" in rl) and "příjemce" not in rl: out = False
    elif "import" in rl: out = False
    elif "úkol" in rl: out = False
    elif any(k in rl for k in _XP_ZERO_KW): out = True
    else: out = False   # skliz/farm
    _cache[r] = out
    return out

con = sqlite3.connect("file:/data/app.db?mode=ro", uri=True); con.row_factory = sqlite3.Row
now = datetime.now(timezone.utc)
monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
month1 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

def board(start_iso, title):
    rows = con.execute(
        "SELECT l.user_id, u.username, l.reason, SUM(l.change) AS g "
        "FROM points_log l JOIN users u ON u.id=l.user_id "
        "WHERE l.change>0 AND l.created_at>=? GROUP BY l.user_id, l.reason", (start_iso,)).fetchall()
    gross, legit = {}, {}
    name = {}
    for r in rows:
        uid = r["user_id"]; name[uid] = r["username"]
        gross[uid] = gross.get(uid, 0) + r["g"]
        if not is_zero(r["reason"]):
            legit[uid] = legit.get(uid, 0) + r["g"]
    g_top = sorted(gross.items(), key=lambda x: -x[1])[:15]
    l_top = sorted(legit.items(), key=lambda x: -x[1])[:15]
    print(f"\n===== {title} (od {start_iso[:10]}) =====")
    print(f"{'#':>2}  {'TED (gross)':<26}{'g':>11}   |  {'NOVY (bez zero)':<26}{'g':>11}")
    for i in range(15):
        gl = f"{name[g_top[i][0]]}" if i < len(g_top) else ""
        gv = f"{g_top[i][1]:,}" if i < len(g_top) else ""
        ll = f"{name[l_top[i][0]]}" if i < len(l_top) else ""
        lv = f"{l_top[i][1]:,}" if i < len(l_top) else ""
        print(f"{i+1:>2}  {gl:<26}{gv:>11}   |  {ll:<26}{lv:>11}")
    # skytlix konkretne
    for uid in gross:
        if name[uid] == "skytlix":
            print(f"\n  skytlix: gross={gross[uid]:,}  ->  legit={legit.get(uid,0):,}  "
                  f"(spadne o {gross[uid]-legit.get(uid,0):,})")

board(monday.isoformat(), "WEEKLY")
board(month1.isoformat(), "SEASON (mesic)")
