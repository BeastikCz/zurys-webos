"""„Den" se počítá podle ČESKÉHO času (Europe/Prague, s DST), ne UTC.

    .venv/Scripts/python.exe -m pytest tests/test_local_time.py -v
"""
from datetime import datetime

from app import db


def test_tzdata_loaded_prague():
    """tzdata musí být k dispozici – jinak fallback na UTC = špatný reset na ČR streamu."""
    assert str(db.LOCAL_TZ) == "Europe/Prague", f"tzdata chybí? LOCAL_TZ={db.LOCAL_TZ}"


def test_local_date_format():
    d = db.local_date()
    assert len(d) == 10 and d.count("-") == 2 and d == db.local_now().date().isoformat()


def test_local_week_id_format():
    w = db.local_week_id()
    assert "-W" in w and w.startswith(str(db.local_now().year))


def test_day_start_is_prague_midnight_in_utc():
    """local_day_start_iso(0) = UTC instant české půlnoci → po převodu zpět do Prahy je 00:00 a dnešní datum."""
    dt_utc = datetime.fromisoformat(db.local_day_start_iso(0))
    assert dt_utc.tzinfo is not None
    prague = dt_utc.astimezone(db.LOCAL_TZ)
    assert prague.hour == 0 and prague.minute == 0 and prague.second == 0
    assert prague.date().isoformat() == db.local_date()


def test_day_start_offsets_differ_by_about_a_day():
    """Včerejšek vs dnešek = ~24 h rozdíl (sanity)."""
    t0 = datetime.fromisoformat(db.local_day_start_iso(0))
    t_1 = datetime.fromisoformat(db.local_day_start_iso(-1))
    diff_h = (t0 - t_1).total_seconds() / 3600
    assert 22 <= diff_h <= 26, diff_h     # 23/24/25 dle DST přechodu
