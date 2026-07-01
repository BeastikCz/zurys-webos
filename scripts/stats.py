import sqlite3
con = sqlite3.connect('file:/data/app.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row

def run(title, sql, fallback=None):
    print('\n== ' + title + ' ==')
    try:
        cur = con.execute(sql)
    except Exception as e:
        if fallback:
            try:
                cur = con.execute(fallback)
            except Exception as e2:
                print('ERR:', e2); return
        else:
            print('ERR:', e); return
    rows = cur.fetchall()
    if not rows:
        print('(zadne radky)'); return
    print(' | '.join(rows[0].keys()))
    for r in rows:
        print(' | '.join('' if r[k] is None else str(r[k]) for k in r.keys()))

run('1) Objednavky podle polozky (vc. smazanych produktu)',
"""SELECT COALESCE(p.name, o.product_name, '(smazano #'||o.product_id||')') AS polozka,
        COUNT(*) AS objednavek, SUM(o.points_spent) AS sedlaku,
        substr(MIN(o.created_at),1,10) AS prvni, substr(MAX(o.created_at),1,10) AS posledni
 FROM orders o LEFT JOIN products p ON p.id=o.product_id
 GROUP BY polozka ORDER BY objednavek DESC LIMIT 100""",
fallback=
"""SELECT COALESCE(p.name, '(smazano #'||o.product_id||')') AS polozka,
        COUNT(*) AS objednavek, SUM(o.points_spent) AS sedlaku,
        substr(MIN(o.created_at),1,10) AS prvni, substr(MAX(o.created_at),1,10) AS posledni
 FROM orders o LEFT JOIN products p ON p.id=o.product_id
 GROUP BY polozka ORDER BY objednavek DESC LIMIT 100""")

run('2) Tomboly: tikety podle produktu',
"""SELECT COALESCE(p.name,'#'||e.product_id) AS tombola, p.type AS typ,
        COUNT(*) AS tiketu, COUNT(DISTINCT e.user_id) AS lidi
 FROM raffle_entries e LEFT JOIN products p ON p.id=e.product_id
 GROUP BY e.product_id ORDER BY tiketu DESC""")

run('3) Vylosovani vyherci (tabulka raffle_winners)',
"""SELECT COALESCE(p.name,'#'||w.product_id) AS tombola, u.username AS vyherce, substr(w.created_at,1,10) AS kdy
 FROM raffle_winners w JOIN users u ON u.id=w.user_id
 LEFT JOIN products p ON p.id=w.product_id ORDER BY w.created_at""")

run('4) Souhrn',
"""SELECT (SELECT COUNT(*) FROM users) AS users,
        (SELECT COUNT(*) FROM orders) AS orders,
        (SELECT COUNT(*) FROM orders WHERE status='fulfilled') AS fulfilled,
        (SELECT COUNT(*) FROM orders WHERE product_id IS NULL) AS legacy_null,
        (SELECT COUNT(*) FROM raffle_entries) AS tikety,
        (SELECT COUNT(*) FROM raffle_winners) AS vyherci_tab,
        (SELECT COUNT(*) FROM products) AS produkty""")

run('5) Kde nejvic utraceno (top polozky dle sedlaku)',
"""SELECT COALESCE(p.name, o.product_name, '(smazano #'||o.product_id||')') AS polozka,
        COUNT(*) AS n, SUM(o.points_spent) AS sedlaku
 FROM orders o LEFT JOIN products p ON p.id=o.product_id
 GROUP BY polozka ORDER BY sedlaku DESC LIMIT 20""",
fallback=
"""SELECT COALESCE(p.name, '(smazano #'||o.product_id||')') AS polozka,
        COUNT(*) AS n, SUM(o.points_spent) AS sedlaku
 FROM orders o LEFT JOIN products p ON p.id=o.product_id
 GROUP BY polozka ORDER BY sedlaku DESC LIMIT 20""")
con.close()
