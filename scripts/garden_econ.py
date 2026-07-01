"""Reálná ekonomika zahrádky z produkce (read-only). Pro ideaci monetizace.
flyctl ssh console -a zurys-shop -C "python3 -" < scripts/garden_econ.py
"""
import sqlite3
from datetime import datetime, timezone, timedelta

con = sqlite3.connect("file:/data/app.db?mode=ro", uri=True); con.row_factory = sqlite3.Row
now = datetime.now(timezone.utc)
d7 = (now - timedelta(days=7)).isoformat()
d30 = (now - timedelta(days=30)).isoformat()

def scalar(q, *a):
    r = con.execute(q, a).fetchone()
    return r[0] if r else 0

print("=== ZAHRÁDKA – PRODUKČNÍ EKONOMIKA ===\n")

# faucet/sink garden (30d)
harv = scalar("SELECT COALESCE(SUM(change),0) FROM points_log WHERE change>0 AND lower(reason) LIKE 'skliz%' AND created_at>=?", d30)
golden = scalar("SELECT COALESCE(SUM(change),0) FROM points_log WHERE change>0 AND reason LIKE 'Zlatý bonus%' AND created_at>=?", d30)
seeds = scalar("SELECT COALESCE(SUM(-change),0) FROM points_log WHERE change<0 AND lower(reason) LIKE 'zasazení:%' AND created_at>=?", d30)
rescue = scalar("SELECT COALESCE(SUM(-change),0) FROM points_log WHERE change<0 AND lower(reason) LIKE 'záchrana%' AND created_at>=?", d30)
decor = scalar("SELECT COALESCE(SUM(-change),0) FROM points_log WHERE change<0 AND lower(reason) LIKE 'dekorace zahrádky%' AND created_at>=?", d30)
print(f"30d: sklizeň(faucet) {harv:,} (+zlatý bonus {golden:,}) | semínka {seeds:,} | záchrana {rescue:,} | dekorace {decor:,}")
print(f"30d NET zahrádka do oběhu: {harv+golden-seeds-rescue-decor:,}  (>0 = inflační faucet)\n")

# engagement: kolik lidí farmí
for label, start in [("7d", d7), ("30d", d30)]:
    n = scalar("SELECT COUNT(DISTINCT user_id) FROM points_log WHERE lower(reason) LIKE 'skliz%' AND created_at>=?", start)
    print(f"aktivních zahradníků {label}: {n}")
tot_users = scalar("SELECT COUNT(*) FROM users")
ever = scalar("SELECT COUNT(DISTINCT user_id) FROM points_log WHERE lower(reason) LIKE 'skliz%'")
print(f"celkem účtů: {tot_users} | kdy-koliv farmili: {ever}\n")

# sub vs non-sub garden gross (7d)
print("--- sub vs non-sub sklizeň (7d) ---")
for r in con.execute(
    "SELECT u.is_sub, COUNT(DISTINCT pl.user_id) lidi, COALESCE(SUM(pl.change),0) gross "
    "FROM points_log pl JOIN users u ON u.id=pl.user_id "
    "WHERE pl.change>0 AND lower(pl.reason) LIKE 'skliz%' AND pl.created_at>=? GROUP BY u.is_sub", (d7,)):
    print(f"  sub={r['is_sub']}: {r['lidi']} lidí, {r['gross']:,} sedláků sklizeno")

# decor uptake (kdo si koupil co – sink + whale signál)
print("\n--- dekorace: kolik vlastníků per tier (all-time) ---")
try:
    for r in con.execute(
        "SELECT decor_key, COUNT(*) n FROM garden_decor GROUP BY decor_key ORDER BY n DESC"):
        print(f"  {r['decor_key']:<12} ×{r['n']}")
except Exception as e:
    print("  (garden_decor:", e, ")")

# expanze záhonů (kolik lidí má >4 plots = koupili budovu)
exp = scalar("SELECT COUNT(DISTINCT user_id) FROM garden_decor WHERE decor_key IN ('manor','stodola','hrad','palac')")
print(f"\nlidí s expanzní budovou (>4 záhony): {exp}")

# golden engagement
gc = scalar("SELECT COUNT(*) FROM points_log WHERE reason LIKE 'Zlatá sklizeň%' AND created_at>=?", d30)
hc = scalar("SELECT COUNT(*) FROM points_log WHERE lower(reason) LIKE 'sklizeň:%' AND created_at>=?", d30)
print(f"\n30d sklizní: {hc:,} normál + {gc:,} zlatých (golden rate {100*gc/max(1,hc+gc):.1f}%)")

# kolik aktuálně roste (živá zahrádka)
growing = scalar("SELECT COUNT(*) FROM garden")
gardeners_now = scalar("SELECT COUNT(DISTINCT user_id) FROM garden")
print(f"teď roste: {growing} záhonů u {gardeners_now} lidí")

# subscribers total (money baseline)
subs = scalar("SELECT COUNT(*) FROM users WHERE is_sub=1")
print(f"\naktivních subů (is_sub=1): {subs}  <-- to je reálná money baseline")
