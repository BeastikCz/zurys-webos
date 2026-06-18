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


def _reset(conn, target, reward, tier_max=10):
    """target = KROK (subů na tier), reward = odměna za tier, tier_max = strop tierů."""
    from app.db import set_setting
    from app import subgoal
    set_setting(conn, "subgoal_enabled", "1")
    set_setting(conn, "subgoal_target", str(target))
    set_setting(conn, "subgoal_reward", str(reward))
    set_setting(conn, "subgoal_tier_max", str(tier_max))
    set_setting(conn, "subgoal_day", subgoal._today())   # dnešní den → žádný reset uprostřed testu
    set_setting(conn, "subgoal_progress", "0")
    set_setting(conn, "subgoal_tier", "0")
    set_setting(conn, "subgoal_done", "0")
    conn.execute("DELETE FROM subgoal_gifters")           # izolace mezi testy (sdílená session DB)
    conn.commit()


def _points(conn, uid):
    return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]


def test_subgoal_escalating_tiers(client):
    """Eskalace: tier 1 (step subů) → reward_step, tier 2 (2×step) → 2×reward_step. Pozdější gifter
    bere vyšší tier; nikdo se nezdvojí; gifter pod 1. milníkem zatím nebere."""
    from app.db import get_conn
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=3, reward=1000)          # krok 3 subů, 1000/tier
        g1 = _mk_user(conn)
        subgoal.record_gifter(conn, g1, 2, in_hh=False)
        conn.commit()
        subgoal.tick(conn, 2)                         # progress 2 < 3 → nic
        assert _points(conn, g1) == 0, "pod 1. milníkem se nevyplácí"
        subgoal.tick(conn, 1)                         # progress 3 → TIER 1 → g1 bere 1000
        assert _points(conn, g1) == 1000
        assert subgoal.status(conn)["tier"] == 1
        g2 = _mk_user(conn)                           # nový gifter, naplní tier 2
        subgoal.record_gifter(conn, g2, 1, in_hh=False)
        conn.commit()
        subgoal.tick(conn, 3)                         # progress 6 → TIER 2 → g2 bere 2000
        assert _points(conn, g2) == 2000, "pozdější gifter bere vyšší tier"
        assert _points(conn, g1) == 1000, "g1 se nezdvojí (paid)"
        assert subgoal.status(conn)["tier"] == 2
    finally:
        conn.close()


def test_subgoal_pays_all_gifters_not_just_hh(client):
    """Odměnu bere KAŽDÝ gifter sub cíle, ne jen happy-hour (HH gating zrušen)."""
    from app.db import get_conn
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=2, reward=1000)
        hh = _mk_user(conn); plain = _mk_user(conn)
        subgoal.record_gifter(conn, hh, 1, in_hh=True)
        subgoal.record_gifter(conn, plain, 1, in_hh=False)
        conn.commit()
        subgoal.tick(conn, 2)                         # progress 2 → TIER 1 → OBA berou 1000
        assert _points(conn, hh) == 1000
        assert _points(conn, plain) == 1000, "gifter mimo HH teď taky bere"
    finally:
        conn.close()


def test_subgoal_tier_cap(client):
    """Strop: tier nepřekročí tier_max, i když progress vyletí výš."""
    from app.db import get_conn
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=2, reward=1000, tier_max=2)   # max tier 2
        g = _mk_user(conn)
        subgoal.record_gifter(conn, g, 10, in_hh=False)
        conn.commit()
        subgoal.tick(conn, 10)                        # progress 10 → tier = min(5, 2) = 2 → 2×1000
        assert _points(conn, g) == 2000, "strop drží tier na tier_max"
        st = subgoal.status(conn)
        assert st["tier"] == 2 and st["maxed"] is True
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


def test_reset_clears_everything(client):
    """subgoal.reset() vynuluje progress, done i seznam gifterů."""
    from app.db import get_conn
    from app import subgoal
    conn = get_conn()
    try:
        _reset(conn, target=5, reward=1000)
        uid = _mk_user(conn)
        subgoal.record_gifter(conn, uid, 2, in_hh=True)
        subgoal.tick(conn, 3)
        conn.commit()
        subgoal.reset(conn)
        conn.commit()
        st = subgoal.status(conn)
        assert st["progress"] == 0 and st["done"] is False and st["gifters"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM subgoal_gifters").fetchone()["c"] == 0
    finally:
        conn.close()


def test_stream_end_resets_subgoal(client, monkeypatch):
    """Přechod LIVE → offline (konec streamu) vynuluje SUB cíl (live_events._check)."""
    from app.db import get_conn, set_setting
    from app import subgoal, live_events
    conn = get_conn()
    try:
        _reset(conn, target=5, reward=1000)
        set_setting(conn, "live_was_live", "1")               # byl live
        set_setting(conn, "subgoal_reset_on_stream_end", "1")
        uid = _mk_user(conn)
        subgoal.record_gifter(conn, uid, 1, in_hh=True)
        subgoal.tick(conn, 4)
        conn.commit()
        monkeypatch.setattr("app.live.is_live", lambda c: False)   # stream skončil
        live_events._check(conn)                              # přechod live→offline → reset
        assert subgoal.status(conn)["progress"] == 0, "konec streamu měl vynulovat lištu"
        assert conn.execute("SELECT COUNT(*) c FROM subgoal_gifters").fetchone()["c"] == 0
    finally:
        conn.close()


def test_stream_end_reset_can_be_disabled(client, monkeypatch):
    """Když subgoal_reset_on_stream_end=0, konec streamu lištu NEvynuluje."""
    from app.db import get_conn, set_setting
    from app import subgoal, live_events
    conn = get_conn()
    try:
        _reset(conn, target=5, reward=1000)
        set_setting(conn, "live_was_live", "1")
        set_setting(conn, "subgoal_reset_on_stream_end", "0")   # vypnuto
        subgoal.tick(conn, 4)
        conn.commit()
        monkeypatch.setattr("app.live.is_live", lambda c: False)
        live_events._check(conn)
        assert subgoal.status(conn)["progress"] == 4, "s vyplým resetem zůstává progress"
    finally:
        set_setting(conn, "subgoal_reset_on_stream_end", "1")   # úklid pro ostatní testy
        conn.commit()
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
