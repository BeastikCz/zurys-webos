"""Komunitní SUB cíl: tick plní cíl, po naplnění odměnu berou JEN dnešní gifteři z happy
hour (ne aktivní diváci, ne gifteři mimo HH). Výplata 1×/den.

    .venv/Scripts/python.exe -m pytest tests/test_subgoal.py -v
"""
import secrets


def _mk_user(conn):
    from app.db import now_iso
    u = f"sg_{secrets.token_hex(3)}"
    return conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
        (u, u, "user", 0, now_iso())).lastrowid


def _reset(conn, target, reward):
    from app.db import set_setting
    from app import subgoal
    set_setting(conn, "subgoal_enabled", "1")
    set_setting(conn, "subgoal_target", str(target))
    set_setting(conn, "subgoal_reward", str(reward))
    set_setting(conn, "subgoal_day", subgoal._today())   # dnešní den → žádný reset uprostřed testu
    set_setting(conn, "subgoal_progress", "0")
    set_setting(conn, "subgoal_done", "0")
    conn.execute("DELETE FROM subgoal_gifters")           # izolace mezi testy (sdílená session DB)
    conn.commit()


def test_subgoal_rewards_only_hh_gifters(client):
    from app.db import get_conn, now_iso, local_date
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=3, reward=4000)
        day = local_date()
        hh = _mk_user(conn)          # giftnul v happy hour → BERE
        plain = _mk_user(conn)       # giftnul mimo HH → nebere
        viewer = _mk_user(conn)      # aktivní divák, negifter → nebere
        conn.execute("INSERT INTO activity_state (user_id, day, watch_today, chat_today) VALUES (?,?,1,0)",
                     (viewer, day))
        conn.commit()

        subgoal.record_gifter(conn, hh, 2, in_hh=True)
        subgoal.record_gifter(conn, plain, 1, in_hh=False)
        conn.commit()

        subgoal.tick(conn, 2)
        assert conn.execute("SELECT points FROM users WHERE id=?", (hh,)).fetchone()["points"] == 0, \
            "pod cílem se nic nevyplácí"

        subgoal.tick(conn, 1)        # 3. sub = dosažení cíle → výplata
        assert subgoal.status(conn)["done"] is True
        assert conn.execute("SELECT points FROM users WHERE id=?", (hh,)).fetchone()["points"] == 4000, \
            "HH gifter bere odměnu"
        assert conn.execute("SELECT points FROM users WHERE id=?", (plain,)).fetchone()["points"] == 0, \
            "gifter mimo happy hour nebere"
        assert conn.execute("SELECT points FROM users WHERE id=?", (viewer,)).fetchone()["points"] == 0, \
            "pasivní aktivní divák nebere"

        subgoal.tick(conn, 5)        # další subby už nevyplácí (1×/den)
        assert conn.execute("SELECT points FROM users WHERE id=?", (hh,)).fetchone()["points"] == 4000
    finally:
        conn.close()


def test_subgoal_no_forfeit_waits_for_hh_gifter(client):
    """Cíl naplněn MIMO HH (0 eligible) → NEdokončuje se (žádný forfeit/lock). Jakmile dorazí HH
    gifter, vyplatí se mu (oprava: dřív cíl 'dohrál' a HH gifteři pak nedostali nic)."""
    from app.db import get_conn
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=2, reward=4000)
        subgoal.tick(conn, 2)                     # cíl naplněn, ale 0 HH gifterů
        assert subgoal.status(conn)["done"] is False, "bez HH gifterů se cíl nedokončuje (žádný forfeit)"
        hh = _mk_user(conn)                       # teď dorazí HH gifter
        subgoal.record_gifter(conn, hh, 1, in_hh=True)
        conn.commit()
        subgoal.tick(conn, 1)                     # gift event → settle vyplatí HH giftera
        assert subgoal.status(conn)["done"] is True
        assert conn.execute("SELECT points FROM users WHERE id=?", (hh,)).fetchone()["points"] == 4000
    finally:
        conn.close()


def test_subgoal_pays_late_hh_gifter_after_completion(client):
    """KAŽDÝ HH gifter dostane odměnu, i když giftne AŽ PO naplnění lišty (oprava lock-outu).
    A nikdo se nezdvojí (paid flag)."""
    from app.db import get_conn
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=2, reward=5000)
        early = _mk_user(conn)
        subgoal.record_gifter(conn, early, 2, in_hh=True)
        conn.commit()
        subgoal.tick(conn, 2)                     # cíl hit → early vyplacen
        assert conn.execute("SELECT points FROM users WHERE id=?", (early,)).fetchone()["points"] == 5000
        late = _mk_user(conn)                     # pozdní HH gifter PO naplnění
        subgoal.record_gifter(conn, late, 1, in_hh=True)
        conn.commit()
        subgoal.tick(conn, 1)                     # další gift event → late taky vyplacen
        assert conn.execute("SELECT points FROM users WHERE id=?", (late,)).fetchone()["points"] == 5000, \
            "pozdní HH gifter taky bere (žádný lock-out)"
        assert conn.execute("SELECT points FROM users WHERE id=?", (early,)).fetchone()["points"] == 5000, \
            "early se nezdvojí (paid=1)"
    finally:
        conn.close()


def test_record_gifter_upsert_counts(client):
    from app.db import get_conn, local_date
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=99, reward=0)
        uid = _mk_user(conn)
        subgoal.record_gifter(conn, uid, 2, in_hh=True)
        subgoal.record_gifter(conn, uid, 3, in_hh=False)    # přičte se k existujícímu řádku
        conn.commit()
        row = conn.execute("SELECT subs, hh_subs FROM subgoal_gifters WHERE day=? AND user_id=?",
                           (local_date(), uid)).fetchone()
        assert row["subs"] == 5 and row["hh_subs"] == 2     # 2 v HH, 3 mimo
        assert subgoal.status(conn)["gifters"] == 1          # 1 HH gifter v hře
    finally:
        conn.close()


def test_subgoal_disabled_does_nothing(client):
    from app.db import get_conn, set_setting
    from app import subgoal
    conn = get_conn()
    try:
        set_setting(conn, "subgoal_enabled", "0")
        set_setting(conn, "subgoal_day", subgoal._today())
        set_setting(conn, "subgoal_progress", "0")
        set_setting(conn, "subgoal_done", "0")
        subgoal.tick(conn, 100)
        assert subgoal.status(conn)["progress"] == 0, "vypnutý cíl se neplní"
    finally:
        conn.close()
