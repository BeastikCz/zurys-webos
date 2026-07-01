import sqlite3, os
DB='/data/app.db'
def human(n):
    for u in ['B','KB','MB','GB','TB']:
        if n<1024: return f"{n:.1f}{u}"
        n/=1024
    return f"{n:.1f}PB"

print('== DISK /data ==')
try:
    s=os.statvfs('/data')
    tot=s.f_blocks*s.f_frsize; free=s.f_bavail*s.f_frsize; used=tot-free
    print(f"total={human(tot)} used={human(used)} free={human(free)} use%={used*100//tot}")
except Exception as e: print('ERR',e)

print('\n== SOUBORY ==')
for f in ['app.db','app.db-wal','app.db-shm']:
    p='/data/'+f
    print(f"{f}: {human(os.path.getsize(p)) if os.path.exists(p) else 'chybi'}")
def dirsize(d):
    t=0
    for root,_,files in os.walk(d):
        for fn in files:
            try: t+=os.path.getsize(os.path.join(root,fn))
            except: pass
    return t
for d in ['/data/uploads']:
    print(f"{d}: {human(dirsize(d)) if os.path.isdir(d) else 'chybi'}")

con=sqlite3.connect('file:%s?mode=ro'%DB, uri=True)
con.row_factory=sqlite3.Row
def one(sql):
    try: return con.execute(sql).fetchone()[0]
    except Exception as e: return 'ERR:%s'%e

print('\n== PRAGMA / INTEGRITA ==')
print('journal_mode:', one('PRAGMA journal_mode'))
print('page_size:', one('PRAGMA page_size'), 'page_count:', one('PRAGMA page_count'), 'freelist:', one('PRAGMA freelist_count'))
print('wal_autocheckpoint:', one('PRAGMA wal_autocheckpoint'))
print('quick_check:', one('PRAGMA quick_check'))
print('foreign_key_check:', con.execute('PRAGMA foreign_key_check').fetchall() or 'OK (0)')

print('\n== ROW COUNTS (velke tabulky) ==')
tabs=['users','points_log','orders','sessions','login_events','admin_audit',
      'raffle_entries','raffle_winners','predictions','prediction_bets',
      'drops','drop_claims','claim_locks','webhook_seen','redeem_codes','products']
for t in tabs:
    print(f"{t}: {one('SELECT COUNT(*) FROM %s'%t)}")

print('\n== SESSIONS (cleanup?) ==')
print('total:', one('SELECT COUNT(*) FROM sessions'))
print('expired (expires_at<now):', one("SELECT COUNT(*) FROM sessions WHERE expires_at < datetime('now')"))
print('oldest last_seen:', one('SELECT MIN(last_seen) FROM sessions'))

print('\n== ANOMALIE ==')
print('users points<0:', one('SELECT COUNT(*) FROM users WHERE points<0'))
print('users earned_total<0:', one('SELECT COUNT(*) FROM users WHERE earned_total<0'))
print('orders points_spent<0:', one('SELECT COUNT(*) FROM orders WHERE points_spent<0'))
print('points_log span:', one('SELECT MIN(created_at) FROM points_log'), '->', one('SELECT MAX(created_at) FROM points_log'))

print('\n== APP_SETTINGS ==')
try:
    for r in con.execute('SELECT * FROM app_settings'):
        print(' |'.join(str(x) for x in r))
except Exception as e: print('ERR',e)
con.close()
print('\n== DONE ==')
