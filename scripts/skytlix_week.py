"""Forenzika: kde se vzalo skytlixovo +X sedláků v týdenním žebříčku.
Zrcadlí přesně /leaderboard/weekly: SUM(change) WHERE change>0 AND created_at >= pondělí 00:00 UTC.
Read-only. Spuštění: Get-Content -Raw scripts/skytlix_week.py | ssh -i "$env:USERPROFILE\.ssh\hetzner_zurys" root@169.58.8.1 "cd /opt/webos/app && WEBOS_DATA_DIR=/data /opt/webos/venv/bin/python -"
"""
import sqlite3
from datetime import datetime, timezone, timedelta

con = sqlite3.connect("file:/data/app.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row

now = datetime.now(timezone.utc)
monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
start = monday.isoformat()
print(f"=== Tyden od {start} (UTC) ===\n")

u = con.execute(
    "SELECT id, username, kick_username, role, points, is_sub, is_vip, prestige, "
    "       earned_total, earned_today, created_at, egg_found_at "
    "FROM users WHERE lower(username)=? OR lower(kick_username)=? LIMIT 1",
    ("skytlix", "skytlix"),
).fetchone()
if not u:
    print("skytlix NENALEZEN")
    raise SystemExit
uid = u["id"]
print(f"user: id={uid} username={u['username']} kick={u['kick_username']} role={u['role']}")
print(f"      zustatek={u['points']:,}  earned_total={u['earned_total']:,}  earned_today={u['earned_today']:,}")
print(f"      sub={u['is_sub']} vip={u['is_vip']} prestige={u['prestige']} created={u['created_at']} egg={u['egg_found_at']}\n")

tot = con.execute(
    "SELECT COALESCE(SUM(change),0) s, COUNT(*) c FROM points_log "
    "WHERE user_id=? AND change>0 AND created_at>=?", (uid, start),
).fetchone()
print(f">>> TYDENNI NASBIRANO (board): {tot['s']:,} sedlaku v {tot['c']} pripsanich\n")

print("--- podle reason (change>0, tento tyden) ---")
for r in con.execute(
    "SELECT reason, COUNT(*) c, SUM(change) s, MAX(change) mx FROM points_log "
    "WHERE user_id=? AND change>0 AND created_at>=? "
    "GROUP BY reason ORDER BY s DESC", (uid, start),
):
    print(f"  {r['s']:>13,}  x{r['c']:<5} max={r['mx']:>11,}  {r['reason']}")

print("\n--- TOP 20 jednotlivych pripsani (tento tyden) ---")
for r in con.execute(
    "SELECT id, change, reason, created_at FROM points_log "
    "WHERE user_id=? AND change>0 AND created_at>=? "
    "ORDER BY change DESC LIMIT 20", (uid, start),
):
    print(f"  id={r['id']:<9} {r['change']:>13,}  {r['created_at']}  {r['reason']}")

print("\n--- admin_audit: rucni sahani na body (all-time, target/details ~ skytlix/uid) ---")
for r in con.execute(
    "SELECT created_at, admin_name, action, target, details FROM admin_audit "
    "WHERE action LIKE '%points%' AND (target LIKE ? OR target LIKE ? OR details LIKE ?) "
    "ORDER BY created_at DESC LIMIT 25",
    (f"%{uid}%", "%skytlix%", "%skytlix%"),
):
    print(f"  {r['created_at']}  by={r['admin_name']}  act={r['action']}  tgt={r['target']}  {r['details']}")
