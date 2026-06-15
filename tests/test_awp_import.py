"""Import ručně dodaných tiketů do tomboly AWP | Printstream.

    .venv/Scripts/python.exe -m pytest tests/test_awp_import.py -v

Ověřuje: správný počet vložených tiketů, idempotenci (2. běh nic nepřidá) a plnou
vratnost přes undo(). Pozn.: část nicků (Itz_Ok, Interaty…) zakládá už navaja import
při startu, takže přesný počet nově založených účtů netestujeme – jen že undo uklidí
právě ty, které založil tenhle import.
"""
from app import awp_import


def _mk_awp(conn, now):
    return conn.execute(
        "INSERT INTO products (name, image_url, cost_points, category, type, stock, "
        "description, active, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("AWP | Printstream (WW)", "", 1000, "Tombola", "raffle", 824, "", 1, now),
    ).lastrowid


def test_awp_import_runs_and_is_reversible(client):
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        # deterministický start: žádná předchozí AWP tombola ani flag
        conn.execute("DELETE FROM app_settings WHERE key=?", (awp_import.FLAG,))
        conn.execute("DELETE FROM products WHERE type='raffle' AND name LIKE '%AWP%' AND name LIKE '%Printstream%'")
        conn.commit()
        pid = _mk_awp(conn, now_iso())
        conn.commit()

        expected = sum(c for _, c in awp_import.TICKETS)   # 56

        res = awp_import.run(conn)
        assert res["tickets_added"] == expected
        assert res["product_id"] == pid

        total = conn.execute("SELECT COUNT(*) c FROM raffle_entries WHERE product_id=?", (pid,)).fetchone()["c"]
        assert total == expected

        # konkrétní nick dostal přesně svůj počet (Itz_Ok = 10)
        uid = conn.execute("SELECT id FROM users WHERE kick_username='itz_ok'").fetchone()["id"]
        n = conn.execute("SELECT COUNT(*) c FROM raffle_entries WHERE product_id=? AND user_id=?",
                         (pid, uid)).fetchone()["c"]
        assert n == 10

        # idempotence: druhý běh nic nepřidá
        assert awp_import.run(conn).get("skipped")
        again = conn.execute("SELECT COUNT(*) c FROM raffle_entries WHERE product_id=?", (pid,)).fetchone()["c"]
        assert again == expected

        # undo: smaže všechny vložené tikety + uklidí prázdné ghosty, které tenhle import založil
        u = awp_import.undo(conn)
        assert u["entries_deleted"] == expected
        assert u["ghosts_deleted"] == res["accounts_created"]
        assert conn.execute("SELECT COUNT(*) c FROM raffle_entries WHERE product_id=?", (pid,)).fetchone()["c"] == 0
    finally:
        conn.close()


def test_awp_import_skips_when_no_product(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        # smaž případnou AWP tombolu z předchozího testu → run musí bezpečně přeskočit
        conn.execute("DELETE FROM products WHERE type='raffle' AND name LIKE '%AWP%' AND name LIKE '%Printstream%'")
        conn.execute("DELETE FROM app_settings WHERE key=?", (awp_import.FLAG,))
        conn.commit()
        assert awp_import.run(conn).get("skipped") == "no AWP raffle"
    finally:
        conn.close()
