"""Levely: XP křivka (čistá funkce) + leaderboard řazení earned vs balance.

    .venv/Scripts/python.exe -m pytest tests/test_levels.py -v
"""
import secrets

from app import levels
from app.db import get_conn, now_iso


def test_level_curve():
    assert levels.level_info(0)["level"] == 0
    assert levels.level_info(300)["level"] >= 1
    assert levels.level_info(110_000)["level"] >= 20      # L20 = 250*20^2 = 100k
    a, b = levels.level_info(50_000), levels.level_info(60_000)
    assert b["level"] >= a["level"]                       # monotónní, jen roste
    assert 0 <= a["pct"] <= 100
    assert a["to_next"] > 0


def test_level_info_safe():
    assert levels.level_info(None)["level"] == 0
    assert levels.level_info(-5)["level"] == 0


def test_leaderboard_earned_vs_balance(client):
    suf = secrets.token_hex(3)
    spender, hoarder = f"ZZspend_{suf}", f"ZZhoard_{suf}"
    conn = get_conn()
    try:
        # spender: málo na účtu, hodně nasbíráno (utratil); hoarder: opačně
        conn.execute("INSERT INTO users (kick_username, username, role, points, earned_total, created_at) VALUES (?,?,?,?,?,?)",
                     (f"a_{suf}", spender, "user", 100, 999_999, now_iso()))
        conn.execute("INSERT INTO users (kick_username, username, role, points, earned_total, created_at) VALUES (?,?,?,?,?,?)",
                     (f"b_{suf}", hoarder, "user", 800_000, 400_000, now_iso()))
        conn.commit()
    finally:
        conn.close()

    earned = client.get("/api/leaderboard?by=earned&limit=200").json()
    re = {r["username"]: r["rank"] for r in earned}
    assert re[spender] < re[hoarder]                      # víc nasbíráno → výš
    assert all("level" in r and "earned" in r for r in earned)

    balance = client.get("/api/leaderboard?by=balance&limit=200").json()
    rb = {r["username"]: r["rank"] for r in balance}
    assert rb[hoarder] < rb[spender]                      # víc na účtu → výš
