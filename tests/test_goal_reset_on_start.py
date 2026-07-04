"""Reset chat/sub cíle na STARTU streamu (s grace oknem) místo konce.

    .venv/Scripts/python.exe -m pytest tests/test_goal_reset_on_start.py -v
"""
from datetime import datetime, timezone, timedelta

from app.db import get_conn, set_setting, get_setting
from app import live_events


def _prep(conn, chat=500, sub=7, offline_min=None):
    """Nastav progres + stav 'offline' (byl offline offline_min minut; None = klíč chybí)."""
    set_setting(conn, "cgoal_progress", str(chat))
    set_setting(conn, "subgoal_progress", str(sub))
    set_setting(conn, "live_was_live", "0")
    if offline_min is None:
        set_setting(conn, "live_went_offline_at", "")
    else:
        set_setting(conn, "live_went_offline_at",
                    (datetime.now(timezone.utc) - timedelta(minutes=offline_min)).isoformat())
    # jistota: reset na startu zapnutý (default), reset na konci vypnutý (prod stav)
    set_setting(conn, "cgoal_reset_on_stream_start", "1")
    set_setting(conn, "subgoal_reset_on_stream_start", "1")
    conn.commit()


def _go_live(conn, monkeypatch):
    monkeypatch.setattr(live_events.live, "is_live", lambda c: True)
    live_events._check(conn)


def test_start_after_long_offline_resets(client, monkeypatch):
    conn = get_conn()
    try:
        _prep(conn, offline_min=120)                      # 2 h offline → nový stream
        _go_live(conn, monkeypatch)
        assert get_setting(conn, "cgoal_progress", "") == "0"
        assert get_setting(conn, "subgoal_progress", "") == "0"
    finally:
        conn.close()


def test_start_after_short_crash_keeps_progress(client, monkeypatch):
    conn = get_conn()
    try:
        _prep(conn, chat=800, sub=5, offline_min=10)      # pád + restart za 10 min → drží
        _go_live(conn, monkeypatch)
        assert get_setting(conn, "cgoal_progress", "") == "800"
        assert get_setting(conn, "subgoal_progress", "") == "5"
    finally:
        conn.close()


def test_start_toggle_off_keeps_progress(client, monkeypatch):
    conn = get_conn()
    try:
        _prep(conn, chat=300, sub=3, offline_min=120)
        set_setting(conn, "cgoal_reset_on_stream_start", "0")
        set_setting(conn, "subgoal_reset_on_stream_start", "0")
        conn.commit()
        _go_live(conn, monkeypatch)
        assert get_setting(conn, "cgoal_progress", "") == "300"
        assert get_setting(conn, "subgoal_progress", "") == "3"
    finally:
        conn.close()


def test_stream_end_records_offline_time(client, monkeypatch):
    conn = get_conn()
    try:
        set_setting(conn, "live_was_live", "1")
        set_setting(conn, "live_went_offline_at", "")
        conn.commit()
        monkeypatch.setattr(live_events.live, "is_live", lambda c: False)
        live_events._check(conn)
        assert get_setting(conn, "live_went_offline_at", ""), "konec streamu má zapsat čas offline"
        assert get_setting(conn, "live_was_live", "") == "0"
    finally:
        conn.close()
