"""Ekonomika za okno 21.6 00:00 -> 29.6 12:00 UTC: vydělano (change>0) vs prodělano/utraceno (change<0).
Den po dni + rozpad kategorií (faucet/sink/gambling). Read-only.
Get-Content -Raw scripts/econ_window.py | ssh -i "$env:USERPROFILE\.ssh\hetzner_zurys" root@169.58.8.1 "cd /opt/webos/app && WEBOS_DATA_DIR=/data /opt/webos/venv/bin/python -"
"""
import sqlite3, json

con = sqlite3.connect("file:/data/app.db?mode=ro", uri=True); con.row_factory = sqlite3.Row
START = "2026-06-21T00:00:00"
END   = "2026-06-29T12:00:00"

# kategorizace dle reason (kopie logiky econ_health._RULES, pořadí = priorita)
_RULES = [
    ("watch","faucet",["sledování stream"]), ("chat","faucet",["aktivita v chatu","komunitní chat cíl"]),
    ("topchat","faucet",["top chatter"]), ("daily","faucet",["denní streak"]),
    ("wheel","faucet",["kolo štěstí"]), ("drops","faucet",["drop #"]), ("codes","faucet",["redeem kód"]),
    ("quests","faucet",["úkol:"]), ("kick","faucet",["kick sub","kick resub","kick gift sub","kick follow","sub cíl"]),
    ("partners","faucet",["partner:","flash partner"]), ("import","faucet",["import ze staré","počáteční body od admina"]),
    ("garden_h","faucet",["sklizeň:","sklizeň ("]),
    ("shop","sink",["nákup odměn","nákup"]), ("garden_s","sink",["zasazení:"]),
    ("garden_d","sink",["dekorace zahrádky"]), ("prestige","sink",["prestige"]),
    ("predictions","gambling",["predikce"]), ("blackjack","gambling",["blackjack"]), ("mines","gambling",["mines"]),
    ("games","gambling",["piškvor","duel","remíz","coinflip","kostky","kámen-nůžky","hra #","vklad","výzv","záchrana"]),
    ("gifts","transfer",["dar pro","dar od","dar →","vrácení daru"]),
    ("golden","faucet",["zlatý bonus"]),
]
def kind_of(reason):
    r = (reason or "").lower()
    for key, kind, subs in _RULES:
        if any(s in r for s in subs):
            return key, kind
    return "other", "other"

# --- denní řada ---
print("=== DENNÍ (UTC) ===")
print(f"{'den':<12}{'vydělano':>14}{'prodělano/utr':>16}{'net':>14}{'lidí':>7}")
daily = []
tot_in = tot_out = 0
for r in con.execute(
    "SELECT substr(created_at,1,10) d, "
    "SUM(CASE WHEN change>0 THEN change ELSE 0 END) minted, "
    "SUM(CASE WHEN change<0 THEN -change ELSE 0 END) burned, "
    "COUNT(DISTINCT user_id) dau "
    "FROM points_log WHERE created_at>=? AND created_at<? GROUP BY d ORDER BY d", (START, END)):
    net = r["minted"] - r["burned"]
    daily.append({"d": r["d"], "in": r["minted"], "out": r["burned"], "net": net})
    tot_in += r["minted"]; tot_out += r["burned"]
    print(f"{r['d']:<12}{r['minted']:>14,}{r['burned']:>16,}{net:>14,}{r['dau']:>7}")
print(f"{'CELKEM':<12}{tot_in:>14,}{tot_out:>16,}{tot_in-tot_out:>14,}")

# --- rozpad kategorií ---
print("\n=== KATEGORIE (celé okno) ===")
cats = {}
for r in con.execute(
    "SELECT reason, SUM(CASE WHEN change>0 THEN change ELSE 0 END) i, "
    "SUM(CASE WHEN change<0 THEN -change ELSE 0 END) o "
    "FROM points_log WHERE created_at>=? AND created_at<? AND change!=0 GROUP BY reason", (START, END)):
    key, kind = kind_of(r["reason"])
    c = cats.setdefault(kind, {"in": 0, "out": 0})
    c["in"] += r["i"]; c["out"] += r["o"]
for kind in ("faucet", "gambling", "sink", "transfer", "other"):
    c = cats.get(kind)
    if c:
        print(f"  {kind:<10} vydělano {c['in']:>13,}  utraceno/prohra {c['out']:>13,}  net {c['in']-c['out']:>+13,}")

# JSON pro graf
print("\n@@JSON@@" + json.dumps({"daily": daily, "tot_in": tot_in, "tot_out": tot_out,
      "cats": {k: cats[k] for k in cats}}))
