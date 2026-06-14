"""Happy Hour: ruční start/stop + násobič platí i s VYPLÝM auto (livehappy_enabled=0).

    .venv/Scripts/python.exe -m pytest tests/test_happy_manual.py -v
"""


def test_manual_start_applies_mult_regardless_of_enabled(client):
    from app.db import get_conn, set_setting
    from app import live_events
    conn = get_conn()
    try:
        set_setting(conn, "livehappy_enabled", "0")   # auto na startu VYPLÉ
        set_setting(conn, "livehappy_mult", "2")
        set_setting(conn, "livehappy_minutes", "5")
        set_setting(conn, "happy_until", "")
        conn.commit()

        assert live_events.happy_mult(conn) == 1.0, "nic neběží → 1.0"

        live_events.start_now(conn)                    # RUČNÍ spuštění (i když auto je vyplé)
        assert live_events.happy_mult(conn) == 2.0, "ruční HH dá mult i s vyplým auto"

        live_events.stop_now(conn)
        assert live_events.happy_mult(conn) == 1.0, "po stopu zase 1.0"
    finally:
        conn.close()
