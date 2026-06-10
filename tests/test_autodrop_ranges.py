"""Auto-drop rozsahy „od–do" (anti-timing): web losuje náhodný interval/body/
výherce v rozmezí, ať diváci drop nenačasují. Testuje čistou logiku rozsahů,
losování a normalizaci (DO nikdy < OD).

    .venv/Scripts/python.exe -m pytest tests/test_autodrop_ranges.py -v
"""
from app import autodrop
from app.db import get_conn


def test_roll_fixed_when_hi_not_greater_than_lo():
    assert autodrop._roll(5, 5) == 5
    assert autodrop._roll(7, 3) == 7        # hi < lo → vrátí lo (fixní hodnota)


def test_roll_stays_within_range():
    for _ in range(300):
        assert 2 <= autodrop._roll(2, 8) <= 8


def test_config_has_range_keys(client):
    """get_config vždy vrací *_max + next_interval a DO není menší než OD."""
    conn = get_conn()
    try:
        cfg = autodrop.get_config(conn)
        for k in ("autodrop_interval_max", "autodrop_points_max",
                  "autodrop_winners_max", "next_interval"):
            assert k in cfg, f"chybí klíč {k}"
        assert cfg["autodrop_interval_max"] >= cfg["autodrop_interval_min"]
        assert cfg["autodrop_points_max"] >= cfg["autodrop_points"]
        assert cfg["autodrop_winners_max"] >= cfg["autodrop_winners"]
    finally:
        conn.close()


def test_set_config_roundtrip_ranges(client):
    conn = get_conn()
    try:
        autodrop.set_config(conn, {
            "autodrop_interval_min": 20, "autodrop_interval_max": 40,
            "autodrop_points": 300, "autodrop_points_max": 800,
            "autodrop_winners": 3, "autodrop_winners_max": 7,
        })
        cfg = autodrop.get_config(conn)
        assert (cfg["autodrop_interval_min"], cfg["autodrop_interval_max"]) == (20, 40)
        assert (cfg["autodrop_points"], cfg["autodrop_points_max"]) == (300, 800)
        assert (cfg["autodrop_winners"], cfg["autodrop_winners_max"]) == (3, 7)
    finally:
        conn.close()


def test_get_config_normalizes_max_below_from(client):
    """DO menší než OD → get_config srovná DO na OD (fixní, nepadne to)."""
    conn = get_conn()
    try:
        autodrop.set_config(conn, {
            "autodrop_interval_min": 40, "autodrop_interval_max": 20,
        })
        cfg = autodrop.get_config(conn)
        assert cfg["autodrop_interval_max"] == 40
    finally:
        conn.close()
