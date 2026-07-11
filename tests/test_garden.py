"""Zahrádka: zasaď (zaplať sazbu) → po dorostení sklidíš (odměna).

    .venv/Scripts/python.exe -m pytest tests/test_garden.py -v
"""
import secrets


def _mk(conn, points=500):
    from app.db import now_iso
    u = f"g_{secrets.token_hex(3)}"
    uid = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
                       (u, u, "user", points, now_iso())).lastrowid
    conn.commit()
    return uid


def test_garden_plant_grow_harvest(client, monkeypatch):
    from app.db import get_conn
    from app import garden
    monkeypatch.setattr(garden, "GOLDEN_CHANCE", 0)   # deterministicky bez zlaté sklizně
    conn = get_conn()
    try:
        uid = _mk(conn, points=500)
        user = {"id": uid}
        st = garden.status(conn, user)
        assert len(st["plots"]) == garden.N_PLOTS and st["plots"][0]["empty"]

        r = garden.plant(conn, user, 0, "mrkev")        # semínko 38 (75 % z výnosu 50)
        assert r["ok"] and r["balance"] == 462
        assert garden.plant(conn, user, 0, "mrkev")["ok"] is False     # obsazený
        assert garden.harvest(conn, user, 0)["ok"] is False            # nedorostlo

        # nasimuluj dorostení (pest=0 → deterministicky bez škůdců, plná sklizeň)
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0, pest_at=NULL WHERE user_id=? AND plot=0", (uid,))
        conn.commit()
        h = garden.harvest(conn, user, 0)
        assert h["ok"] and h["reward"] == 50 and h["balance"] == 462 + 50
        assert garden.status(conn, user)["plots"][0]["empty"]          # zase volný

        # málo sedláků na klas (semínko 1050 = 75 % z 1400) → fail
        conn.execute("UPDATE users SET points=10 WHERE id=?", (uid,)); conn.commit()
        assert garden.plant(conn, user, 1, "klas")["ok"] is False
        assert garden.plant(conn, user, 1, "neznama")["ok"] is False   # neznámá plodina
    finally:
        conn.close()


def test_garden_pest_rescue_and_penalty(client, monkeypatch):
    """Škůdci: zachráníš (zaplať 25 % výnosu) → plná sklizeň; neošetříš → jen půlka."""
    from app.db import get_conn
    from app import garden
    monkeypatch.setattr(garden, "GOLDEN_CHANCE", 0)   # deterministicky bez zlaté sklizně
    conn = get_conn()
    try:
        uid = _mk(conn, points=3000)
        user = {"id": uid}
        import datetime as _dt
        garden.plant(conn, user, 0, "klas")
        garden.plant(conn, user, 1, "klas")
        now = _dt.datetime.now(_dt.timezone.utc)
        active_at = (now - _dt.timedelta(minutes=1)).isoformat()    # objevili se před chvílí → AKTIVNÍ (okno ~7 h)
        eaten_at = (now - _dt.timedelta(hours=100)).isoformat()     # dávno → po okně → SEŽRÁNO
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0, pest_at=? WHERE user_id=? AND plot=0", (active_at, uid))
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0, pest_at=? WHERE user_id=? AND plot=1", (eaten_at, uid))
        conn.commit()
        p0 = garden.status(conn, user)["plots"][0]
        assert p0["pest"] is True and p0["rescue_cost"] == 280   # 20 % z 1400, aktivní
        p1 = garden.status(conn, user)["plots"][1]
        assert p1["eaten"] is True and p1["pest"] is False

        # plot 0: zachraň v okně → plná sklizeň 1400
        rr = garden.rescue(conn, user, 0)
        assert rr["ok"] and rr["cost"] == 280
        h0 = garden.harvest(conn, user, 0)
        assert h0["ok"] and h0["pest"] is False and h0["reward"] == 1400

        # plot 1: po okně → rescue zamčen + sklizeň jen půlka (700)
        assert garden.rescue(conn, user, 1)["ok"] is False
        h1 = garden.harvest(conn, user, 1)
        assert h1["ok"] and h1["pest"] is True and h1["reward"] == 700

        # rescue na záhonu bez chrobáků = fail
        garden.plant(conn, user, 2, "mrkev")
        conn.execute("UPDATE garden SET pest_at=NULL WHERE user_id=? AND plot=2", (uid,)); conn.commit()
        assert garden.rescue(conn, user, 2)["ok"] is False
    finally:
        conn.close()


def test_garden_decor_buy(client):
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=3000)
        user = {"id": uid}
        st = garden.decor_status(conn, user)
        assert len(st["items"]) == len(garden.DECOR) and not st["owned_icons"]
        assert st["pest_chance"] == 85
        assert next(i for i in st["items"] if i["key"] == "sunflower")["pest_reduction"] == 2

        r = garden.buy_decor(conn, user, "sunflower")   # cost 500
        assert r["ok"] and r["balance"] == 2500
        assert garden.buy_decor(conn, user, "sunflower")["ok"] is False     # už vlastní
        st2 = garden.decor_status(conn, user)
        assert "🌻" in st2["owned_icons"]
        assert st2["pest_reduction"] == 2 and st2["pest_chance"] == 83   # 85 − 2 (slunečnice)
        assert garden.status(conn, user)["pest_chance"] == 83

        assert garden.buy_decor(conn, user, "rainbow")["ok"] is False       # 9000 > 2500
        assert garden.buy_decor(conn, user, "neznama")["ok"] is False       # neznámá
    finally:
        conn.close()


def test_garden_bulk_and_golden(client, monkeypatch):
    """Zasadit vše / Sklidit vše + zlatá (vzácná) sklizeň ×3."""
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=100000)
        user = {"id": uid}
        monkeypatch.setattr(garden, "GOLDEN_CHANCE", 0)        # bez zlaté pro deterministické bulk asserty
        # Zasadit vše (mrkev) na všechny prázdné záhony
        r = garden.plant_all(conn, user, "mrkev")
        assert r["ok"] and r["planted"] == garden.N_PLOTS
        assert garden.plant_all(conn, user, "mrkev")["ok"] is False   # už plno
        # dorostit + bez škůdců → Sklidit vše
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0, pest_at=NULL WHERE user_id=?", (uid,)); conn.commit()
        h = garden.harvest_all(conn, user)
        assert h["ok"] and h["count"] == garden.N_PLOTS and h["total"] == garden.N_PLOTS * 50 and h["golden"] == 0
        # Zlatá sklizeň: vždy golden → ×3 výnos
        monkeypatch.setattr(garden, "GOLDEN_CHANCE", 1.0)
        garden.plant(conn, user, 0, "mrkev")
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0, pest_at=NULL WHERE user_id=? AND plot=0", (uid,)); conn.commit()
        et_before = conn.execute("SELECT earned_total FROM users WHERE id=?", (uid,)).fetchone()[0]
        hg = garden.harvest(conn, user, 0)
        et_after = conn.execute("SELECT earned_total FROM users WHERE id=?", (uid,)).fetchone()[0]
        from app.deps import GARDEN_XP_FACTOR
        assert hg["golden"] is True and hg["reward"] == 50 * garden.GOLDEN_MULT        # ×3 SEDLÁCI
        assert et_after - et_before == round(50 * GARDEN_XP_FACTOR)                    # ale XP jen z base (1×, neměň XP)
    finally:
        conn.close()


def test_garden_harvest_all_respects_pest(client, monkeypatch):
    """Regrese (HIGH): Sklidit vše NESMÍ obejít chrobáky – sežrané = půlka, ne plný výnos."""
    import datetime as _dt
    from app.db import get_conn
    from app import garden
    monkeypatch.setattr(garden, "GOLDEN_CHANCE", 0)
    conn = get_conn()
    try:
        uid = _mk(conn, points=10000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "klas")
        eaten = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=100)).isoformat()
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest=0, pest_at=? WHERE user_id=? AND plot=0", (eaten, uid)); conn.commit()
        h = garden.harvest_all(conn, user)
        assert h["count"] == 1 and h["total"] == 700   # klas 1400 sežráno → půlka, NE plných 1400
    finally:
        conn.close()


def test_garden_notify_ready_and_pest(client):
    """Daemon scan: zralá plodina → notif 🌾; aktivní chrobáci → notif 🐛; 2. scan už nic (anti-spam)."""
    import datetime as _dt
    from app.db import get_conn
    from app import garden, garden_notify
    conn = get_conn()
    try:
        uid = _mk(conn, points=5000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "mrkev")   # bude zralá
        garden.plant(conn, user, 1, "klas")    # chrobáci aktivní (ještě neroste hotová)
        active_at = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE garden SET ready_at='2000-01-01T00:00:00+00:00', pest_at=NULL, notified=0 WHERE user_id=? AND plot=0", (uid,))
        conn.execute("UPDATE garden SET pest=0, pest_at=?, notified=0 WHERE user_id=? AND plot=1", (active_at, uid))
        conn.commit()
        garden_notify._scan(conn)
        icons = {n["icon"] for n in conn.execute("SELECT icon FROM notifications WHERE user_id=?", (uid,)).fetchall()}
        assert "🌾" in icons and "🐛" in icons
        n_before = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id=?", (uid,)).fetchone()["c"]
        garden_notify._scan(conn)              # 2. scan → nic nového (notified bit zabrání spamu)
        n_after = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id=?", (uid,)).fetchone()["c"]
        assert n_after == n_before
    finally:
        conn.close()


def test_garden_incoming_exposes_refresh_deadline(client):
    """Otevřený frontend dostane čas příchodu a může útok překreslit bez reloadu stránky."""
    import datetime as _dt
    from app.db import get_conn
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=5000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "mrkev")
        incoming = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=30)).isoformat()
        conn.execute("UPDATE garden SET pest=0, pest_at=? WHERE user_id=? AND plot=0", (incoming, uid))
        conn.commit()

        plot = garden.status(conn, user)["plots"][0]
        assert plot["pest"] is False and plot["eaten"] is False
        assert 1 <= plot["pest_in"] <= 30

        from app.config import WEB_DIR
        js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert 'data-refresh-left="${p.pest_in}"' in js
        assert 'document.querySelectorAll("[data-refresh-left]")' in js
    finally:
        conn.close()


def test_garden_legacy_pest_is_migrated(client):
    """Garden v1 pest=1 bez pest_at nesmí po upgradu dostat plnou sklizeň zdarma."""
    import datetime as _dt
    from app.db import get_conn, init_db
    from app import garden
    conn = get_conn()
    try:
        uid = _mk(conn, points=5000)
        user = {"id": uid}
        garden.plant(conn, user, 0, "mrkev")
        planted = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
        conn.execute(
            "UPDATE garden SET planted_at=?, ready_at=?, pest=1, pest_at=NULL WHERE user_id=? AND plot=0",
            (planted, planted, uid),
        )
        conn.commit()
    finally:
        conn.close()

    init_db()
    conn = get_conn()
    try:
        row = conn.execute("SELECT pest,pest_at FROM garden WHERE user_id=? AND plot=0", (uid,)).fetchone()
        assert row["pest"] == 0 and row["pest_at"] == planted
        plot = garden.status(conn, user)["plots"][0]
        assert plot["eaten"] is True
    finally:
        conn.close()


def test_maintenance_freezes_garden(client):
    """Údržba zamrazí zahrádku: po vypnutí se planted/ready/pest_at posunou o délku výpadku."""
    import datetime as _dt
    from app.db import get_conn, set_setting
    from app import garden, maintenance
    conn = get_conn()
    try:
        uid = _mk(conn)
        garden.plant(conn, {"id": uid}, 0, "mrkev")
        # zasazeno 20 min PŘED začátkem údržby (posun se týká jen před-údržbových záhonů)
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=20)).isoformat()
        conn.execute("UPDATE garden SET planted_at=?, ready_at=?, pest_at=? WHERE user_id=?",
                     (old, old, old, uid)); conn.commit()
        before = dict(conn.execute("SELECT planted_at, ready_at FROM garden WHERE user_id=?", (uid,)).fetchone())

        # údržba začala před 10 minutami
        maintenance.set_on(conn, True)
        since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=10)).isoformat()
        set_setting(conn, "maintenance_since", since); conn.commit()
        maintenance.set_on(conn, False)

        after = conn.execute("SELECT planted_at, ready_at, pest_at FROM garden WHERE user_id=?", (uid,)).fetchone()
        shift = _dt.datetime.fromisoformat(after["ready_at"]) - _dt.datetime.fromisoformat(before["ready_at"])
        assert 9.5 * 60 < shift.total_seconds() < 10.5 * 60
        assert after["pest_at"] == after["ready_at"]   # NULL-safe posun proběhl i u pest_at
    finally:
        conn.execute("DELETE FROM garden WHERE user_id=?", (uid,)); conn.commit()
        conn.close()
