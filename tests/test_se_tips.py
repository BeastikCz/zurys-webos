"""StreamElements donaty: parse + baseline kurzor + dedup + feed pro overlay.

    .venv/Scripts/python.exe -m pytest tests/test_se_tips.py -v
"""


def _doc(se_id, ts, amount=100, name="DonátorCZ", currency="CZK", msg="Jen tak dál!"):
    return {"_id": se_id, "createdAt": ts,
            "donation": {"user": {"username": name}, "amount": amount,
                         "currency": currency, "message": msg}}


def test_parse_tip_shapes():
    from app.se_tips import parse_tip
    t = parse_tip(_doc("a1", "2026-07-01T10:00:00.000Z"))
    assert t == {"se_id": "a1", "ts": "2026-07-01T10:00:00.000Z", "name": "DonátorCZ",
                 "amount": 100.0, "currency": "CZK", "message": "Jen tak dál!"}
    # top-level fallback (SE shape kolísá)
    t2 = parse_tip({"id": "b2", "amount": "50", "username": "X", "currency": "EUR"})
    assert t2["se_id"] == "b2" and t2["amount"] == 50.0 and t2["currency"] == "EUR"
    assert parse_tip({"_id": "c", "donation": {"amount": "nic"}}) is None
    assert parse_tip({"_id": "d", "donation": {"amount": -5}}) is None
    assert parse_tip("blbost") is None


def test_store_tips_baseline_then_new_only(client):
    from app.db import get_conn, get_setting
    from app.se_tips import store_tips
    conn = get_conn()
    try:
        conn.execute("DELETE FROM donations")
        conn.execute("DELETE FROM app_settings WHERE key='se_tips_last_ts'")
        conn.commit()
        # 1. běh = baseline: historie se NEukládá, jen kurzor
        n = store_tips(conn, [_doc("h1", "2026-07-01T09:00:00Z"), _doc("h2", "2026-07-01T09:30:00Z")])
        assert n == 0
        assert conn.execute("SELECT COUNT(*) c FROM donations").fetchone()["c"] == 0
        assert get_setting(conn, "se_tips_last_ts", "") == "2026-07-01T09:30:00Z"
        # 2. běh: starý tip ignorován, nový uložen; re-poll stejného = dedup přes se_id
        batch = [_doc("h2", "2026-07-01T09:30:00Z"), _doc("n1", "2026-07-01T10:00:00Z", amount=500)]
        assert store_tips(conn, batch) == 1
        assert store_tips(conn, batch) == 0     # kurzor už posunutý → nic nového
        row = conn.execute("SELECT * FROM donations").fetchone()
        assert row["se_id"] == "n1" and row["amount"] == 500.0
    finally:
        conn.close()


def test_recent_events_donate_cursor(client):
    from app.db import get_conn, now_iso
    from app.subgoal import recent_events
    conn = get_conn()
    try:
        conn.execute("DELETE FROM donations")
        conn.execute("INSERT INTO donations (se_id, name, amount, currency, message, created_at) VALUES "
                     "('x1','Pepa',69,'CZK','',?)", (now_iso(),))
        conn.commit()
        base = recent_events(conn)                       # baseline: kurzory, žádné eventy
        assert base["donates"] == [] and base["don_latest_id"] >= 1
        d = recent_events(conn, since=base["latest_id"], don_since=0)
        assert any(x["kind"] == "donate" and x["username"] == "Pepa" and x["amount"] == 69
                   for x in d["donates"])
        d2 = recent_events(conn, since=base["latest_id"], don_since=base["don_latest_id"])
        assert d2["donates"] == []                       # kurzor za posledním → nic
    finally:
        conn.close()
