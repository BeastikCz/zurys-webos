"""Crew / Parta (klany) P1: založení (sink), 1 parta/hráč, join + plno, odchod
(rozpuštění / předání vůdce), crew XP hook (cap + týdenní reset), žebříček, chat.

    .venv/Scripts/python.exe -m pytest tests/test_crews.py -v
"""
import secrets

from app.db import get_conn, now_iso, local_week_id
from app import crews


def _mk_user(points=100000):
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"crew_{suf}", f"crew_{suf}", "user", points, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _points(uid):
    conn = get_conn()
    try:
        return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"]
    finally:
        conn.close()


def _run(fn, *a):
    conn = get_conn()
    try:
        return fn(conn, *a)
    finally:
        conn.close()


def _commit(fn, *a):
    conn = get_conn()
    try:
        r = fn(conn, *a)
        conn.commit()
        return r
    finally:
        conn.close()


def _member(uid):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM crew_members WHERE user_id=?", (uid,)).fetchone()
    finally:
        conn.close()


def _crew_exists(cid):
    conn = get_conn()
    try:
        return conn.execute("SELECT 1 FROM crews WHERE id=?", (cid,)).fetchone() is not None
    finally:
        conn.close()


def test_create_debits_and_leader(client):
    u = _mk_user(100000)
    st = _run(crews.create, u, "user", "Sedlaci Pepy", "PEP")
    assert st["tag"] == "PEP" and st["is_leader"] and st["members_count"] == 1
    assert _points(u) == 100000 - crews.FOUND_COST
    assert _member(u)["role"] == "leader"


def test_create_needs_funds(client):
    u = _mk_user(100)
    try:
        _run(crews.create, u, "user", "Chudaci", "CHU")
        assert False, "bez sedláků nejde založit"
    except ValueError as e:
        assert "sedlák" in str(e).lower()


def test_one_crew_per_user(client):
    u = _mk_user(100000)
    _run(crews.create, u, "user", "Parta A", "AAA")
    try:
        _run(crews.create, u, "user", "Parta B", "BBB")
        assert False, "1 hráč = 1 parta"
    except ValueError as e:
        assert "partě" in str(e).lower()


def test_join_and_duplicate(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "JoinTest", "JNT")
    p = _mk_user(100000); st2 = _run(crews.join, p, "p", st["code"])
    assert st2["members_count"] == 2
    try:
        _run(crews.join, p, "p", st["code"])
        assert False, "už v partě"
    except ValueError:
        pass


def test_cap_slots_schedule(client):
    """Sloty: lvl 5 → 7 míst, další až lvl 15 (pak 25). Řidší = exkluzivní velké party."""
    h = _mk_user(100000); st = _run(crews.create, h, "host", "SlotParta", "SLT")
    for i in range(crews.MEMBER_CAP - 1):                  # doplň na základní cap 6
        _run(crews.join, _mk_user(1000), "m%d" % i, st["code"])
    extra = _mk_user(1000)
    try:
        _run(crews.join, extra, "extra", st["code"])
        assert False, "na lvl 1 je 6/6 plno"
    except ValueError as e:
        assert "plná" in str(e)
    conn = get_conn()                                      # vylevluj partu na lvl 5 (sqrt křivka)
    conn.execute("UPDATE crews SET xp=? WHERE id=?", (16 * crews.CREW_LEVEL_BASE, st["id"]))
    conn.commit(); conn.close()
    st2 = _run(crews.join, extra, "extra", st["code"])     # lvl 5 → 7. místo otevřené
    assert st2["level"] == 5 and st2["member_cap"] == crews.MEMBER_CAP + 1
    assert st2["members_count"] == 7 and st2["next_slot_level"] == 15   # další slot až lvl 15 (ne 10)
    try:
        _run(crews.join, _mk_user(1000), "extra2", st["code"])
        assert False, "7/7 zase plno (lvl 10 už NEotvírá slot)"
    except ValueError:
        pass
    conn = get_conn()                                      # lvl 15 → 8. místo
    conn.execute("UPDATE crews SET xp=? WHERE id=?", (196 * crews.CREW_LEVEL_BASE, st["id"]))
    conn.commit(); conn.close()
    st3 = _run(crews.join, _mk_user(1000), "eightth", st["code"])
    assert st3["level"] == 15 and st3["member_cap"] == crews.MEMBER_CAP + 2
    assert st3["members_count"] == 8 and st3["next_slot_level"] == 25


def test_join_bad_code(client):
    p = _mk_user(100000)
    try:
        _run(crews.join, p, "p", "PZZZZZZ")
        assert False, "neexistující kód"
    except ValueError as e:
        assert "neexistuje" in str(e).lower()


def test_leave_disbands_solo(client):
    u = _mk_user(100000); st = _run(crews.create, u, "u", "Solo", "SOL")
    _run(crews.leave, u)
    assert _member(u) is None and not _crew_exists(st["id"])


def test_leave_transfers_leader(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "Transfer", "TRF")
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    _run(crews.leave, h)                                  # vůdce odejde
    assert _crew_exists(st["id"])
    assert _member(p)["role"] == "leader"                 # předáno nejstaršímu zbylému


def test_contribute_capped(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "Farmari", "FRM")
    _commit(crews.contribute, u, 3000)
    assert _member(u)["week_xp"] == 3000
    _commit(crews.contribute, u, crews.WEEK_XP_CAP)       # přes cap
    assert _member(u)["week_xp"] == crews.WEEK_XP_CAP      # week_xp ořezáno na cap (fér žebříček)
    assert _member(u)["contributed"] == 3000 + crews.WEEK_XP_CAP   # contributed UNcapped = kolik fakt přispěl


def test_sub_feeds_crew_via_add_points(client):
    from app.deps import add_points, XP_PER_SUB
    u = _mk_user(100000); _run(crews.create, u, "u", "SubParta", "SUB")
    conn = get_conn()
    try:
        add_points(conn, u, 1000, "Kick sub")             # supporter event → XP_PER_SUB do crew
        conn.commit()
    finally:
        conn.close()
    assert _member(u)["contributed"] == XP_PER_SUB         # sub přispěl do party (vč. levelu hráče)


def test_gift_subs_uncapped_weekly(client):
    from app.deps import add_points, XP_PER_SUB
    u = _mk_user(100000); _run(crews.create, u, "u", "GiftParta", "GFT")
    conn = get_conn()
    try:
        add_points(conn, u, 5000, "Kick gift sub 3x")     # 3 darované suby → 3×XP_PER_SUB
        conn.commit()
    finally:
        conn.close()
    m = _member(u)
    assert m["contributed"] == 3 * XP_PER_SUB              # all-time celkem
    assert m["sub_xp"] == 3 * XP_PER_SUB                   # sub contribution zvlášť (supporter)
    assert m["week_xp"] == 3 * XP_PER_SUB                  # suby UNcapped i týdně (velký gifter září, ne flat na capu)


def test_sub_bypasses_weekly_farm_cap(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "MixParta", "MIX")
    _commit(crews.contribute, u, crews.WEEK_XP_CAP + 5000)         # farm přes cap
    assert _member(u)["week_xp"] == crews.WEEK_XP_CAP               # farm týdně capnutý
    _commit(crews.contribute, u, 5000, True)                       # sub (is_sub=True)
    m = _member(u)
    assert m["week_xp"] == crews.WEEK_XP_CAP + 5000                 # sub se přičte NAD farm cap
    assert m["sub_xp"] == 5000                                      # sub_xp sledováno zvlášť


def _set_crew_xp(uid, xp):
    conn = get_conn()
    try:
        cid = conn.execute("SELECT crew_id FROM crew_members WHERE user_id=?", (uid,)).fetchone()["crew_id"]
        conn.execute("UPDATE crews SET xp=? WHERE id=?", (xp, cid)); conn.commit()
    finally:
        conn.close()


def test_earn_bonus_scales_and_sub_beats_farm(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "BonusParta", "BON")
    conn = get_conn()
    try:
        assert crews.earn_bonus(conn, u, "sub") == 1.0 and crews.earn_bonus(conn, u, "farm") == 1.0   # lvl1 = bez bonusu
    finally:
        conn.close()
    _set_crew_xp(u, 108000)                                         # → level 4 (křivka /12000: 9×12000)
    conn = get_conn()
    try:
        assert abs(crews.earn_bonus(conn, u, "sub") - 1.06) < 1e-9    # lvl4 → +6 % sub
        assert abs(crews.earn_bonus(conn, u, "farm") - 1.015) < 1e-9  # lvl4 → +1,5 % farm
        assert crews.earn_bonus(conn, u, "sub") > crews.earn_bonus(conn, u, "farm")   # sub bonus > farm
    finally:
        conn.close()


def test_earn_bonus_caps(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "CapParta", "CAP")
    _set_crew_xp(u, 99999999)                                       # extrémní level
    conn = get_conn()
    try:
        assert crews.earn_bonus(conn, u, "sub") == 1.0 + crews.CREW_SUB_BONUS_CAP    # strop +40 %
        assert crews.earn_bonus(conn, u, "farm") == 1.0 + crews.CREW_FARM_BONUS_CAP  # strop +5 %
    finally:
        conn.close()


def test_no_crew_no_bonus(client):
    u = _mk_user(100000)                                            # mimo partu
    conn = get_conn()
    try:
        assert crews.earn_bonus(conn, u, "sub") == 1.0             # bez party = bez bonusu (motivace se přidat)
    finally:
        conn.close()


def test_sub_goal_scales(client):
    assert crews.sub_goal_for(5) == 5                              # 1 sub/člen
    assert crews.sub_goal_for(1) == crews.SUB_GOAL_MIN            # malá parta → minimum


def test_level_bar_matches_level():
    # level bar (level_info s CREW_LEVEL_BASE) MUSÍ dát stejný level jako _level (bonusy) → jinak ⭐ ≠ bar
    from app.deps import level_info
    for xp in (0, 11999, 12000, 47999, 108000, 191999, 1_000_000):
        li = level_info(xp, crews.CREW_LEVEL_BASE)
        assert li["level"] == crews._level(xp)
        assert 0 <= li["pct"] <= 100
        assert li["into"] + (crews.CREW_LEVEL_BASE * (li["level"] - 1) ** 2) == xp   # into = xp − floor


def test_sub_goal_counts_weekly_subs(client):
    from app.deps import add_points
    u = _mk_user(100000); st = _run(crews.create, u, "u", "SubGoalParta", "SGP")
    conn = get_conn()
    try:
        for _ in range(3):                                         # 3 suby → week sub XP = 3×5000
            add_points(conn, u, 1000, "Kick sub")
        conn.commit()
    finally:
        conn.close()
    d = _run(crews.state, u, st["id"])
    assert d["week_subs"] == 3                                     # odvozeno z week_xp − week_farm
    assert d["sub_goal_reached"] is True                          # 3 ≥ 2 (základ, tier 1)
    assert d["sub_tier"] == 1 and d["sub_badge"] == crews.SUB_TIER_BADGES[0]
    assert d["sub_goal"] == crews.sub_goal_for(1) * crews.SUB_TIER_MULTS[1]   # bar míří na tier 2 (2× základ = 4)


def test_claim_goal_hop_proof(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "HopA", "HPA")
    _set_week_xp(u, crews.goal_for(1) + 100)
    _run(crews.claim_goal, u)                                     # claim v partě A
    _run(crews.leave, u)                                         # odejdi (member řádek se smaže)
    conn = get_conn()                                            # cooldown by jinak blokoval join → reset (testujeme claim gate, ne cooldown)
    try:
        conn.execute("UPDATE users SET crew_left_at=NULL WHERE id=?", (u,)); conn.commit()
    finally:
        conn.close()
    h = _mk_user(100000); b = _run(crews.create, h, "h", "HopB", "HPB")
    _run(crews.join, u, "u", b["code"])                           # přidej se do B
    _set_week_xp(u, crews.goal_for(2) + 100)                      # B splní ZÁKLADNÍ cíl
    try:
        _run(crews.claim_goal, u)
        assert False, "hop nesmí umožnit druhý claim tieru 1 za týden"
    except ValueError as e:
        # gate na users → hop-proof: tier 1 už má, v B by šel až tier 2 (2× cíl), který B nemá
        assert "tier 2" in str(e).lower() and "není splněn" in str(e).lower()


def test_kick_removes_member(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "KickParta", "KCK")
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    _run(crews.kick, h, p)                                        # vůdce vyhodí člena
    assert _member(p) is None
    q = _mk_user(100000); _run(crews.join, q, "q", st["code"])
    try:
        _run(crews.kick, q, h)                                    # člen zkusí vyhodit vůdce
        assert False, "jen vůdce může vyhazovat"
    except ValueError:
        pass


def test_set_role_promotes(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "RoleParta", "ROL")
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    _run(crews.set_role, h, p, "officer")
    assert _member(p)["role"] == "officer"
    try:
        _run(crews.set_role, p, h, "member")                     # nevůdce nesmí
        assert False, "jen vůdce mění role"
    except ValueError:
        pass


def test_streak_increments_continues_breaks(client):
    u = _mk_user(100000); st = _run(crews.create, u, "u", "StreakParta", "STK")
    _set_week_xp(u, crews.goal_for(1) + 100)
    _run(crews.claim_goal, u)                                     # 1. claim → streak 1
    conn = get_conn()
    try:
        assert conn.execute("SELECT streak FROM crews WHERE id=?", (st["id"],)).fetchone()["streak"] == 1
        conn.execute("UPDATE crews SET streak=3, streak_week=? WHERE id=?", (crews._prev_week_id(), st["id"]))
        conn.execute("UPDATE users SET crew_goal_week=NULL WHERE id=?", (u,))   # uvolni gate
        conn.commit()
    finally:
        conn.close()
    _run(crews.claim_goal, u)                                     # navázání (minulý týden) → streak 4
    conn = get_conn()
    try:
        assert conn.execute("SELECT streak FROM crews WHERE id=?", (st["id"],)).fetchone()["streak"] == 4
        conn.execute("UPDATE crews SET streak=4, streak_week='2020-W01' WHERE id=?", (st["id"],))   # mezera
        conn.execute("UPDATE users SET crew_goal_week=NULL WHERE id=?", (u,))
        conn.commit()
    finally:
        conn.close()
    _run(crews.claim_goal, u)                                     # mezera → restart na 1
    conn = get_conn()
    try:
        assert conn.execute("SELECT streak FROM crews WHERE id=?", (st["id"],)).fetchone()["streak"] == 1
    finally:
        conn.close()


def test_streak_display_breaks_when_stale(client):
    u = _mk_user(100000); st = _run(crews.create, u, "u", "StaleParta", "STL")
    conn = get_conn()
    try:
        conn.execute("UPDATE crews SET streak=5, streak_week='2020-W01', best_streak=5 WHERE id=?", (st["id"],))
        conn.commit()
    finally:
        conn.close()
    d = _run(crews.state, u, st["id"])
    assert d["streak"] == 0                                       # starý streak_week → zobrazí 0 (přerušeno)
    assert d["best_streak"] == 5                                  # rekord zůstává


def test_join_notifies_members(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "NotifyParta", "NTF")
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND title LIKE '%Nový člen%'",
                         (h,)).fetchone()["c"]
    finally:
        conn.close()
    assert n >= 1                                                # vůdce dostal notifikaci o novém členovi


def test_month_sub_race_lazy_reset(client):
    from app.deps import add_points
    from app.db import local_date
    u = _mk_user(100000); st = _run(crews.create, u, "u", "MonthParta", "MON")
    conn = get_conn()
    try:
        add_points(conn, u, 1000, "Kick gift sub 2x")           # 2 suby → month_sub_xp = 10000
        conn.commit()
        c = conn.execute("SELECT month_sub_xp, month FROM crews WHERE id=?", (st["id"],)).fetchone()
        assert c["month_sub_xp"] == 2 * crews.XP_PER_SUB and c["month"] == local_date()[:7]
        conn.execute("UPDATE crews SET month='2020-01' WHERE id=?", (st["id"],))   # simuluj minulý měsíc
        conn.commit()
        add_points(conn, u, 1000, "Kick sub")                   # nový měsíc → reset
        conn.commit()
        c2 = conn.execute("SELECT month_sub_xp, month FROM crews WHERE id=?", (st["id"],)).fetchone()
        assert c2["month_sub_xp"] == crews.XP_PER_SUB and c2["month"] == local_date()[:7]   # reset, ne 15000
    finally:
        conn.close()


def test_leaderboard_month_sort(client):
    from app.deps import add_points
    a = _mk_user(100000); sa = _run(crews.create, a, "a", "MonthA", "MNA")
    b = _mk_user(100000); sb = _run(crews.create, b, "b", "MonthB", "MNB")
    conn = get_conn()
    try:
        add_points(conn, a, 1000, "Kick gift sub 3x")           # A: 3 suby
        add_points(conn, b, 1000, "Kick sub")                   # B: 1 sub
        conn.commit()
    finally:
        conn.close()
    lb = _run(crews.leaderboard, a, 50, "month")
    ids = [c["id"] for c in lb["crews"]]
    assert ids.index(sa["id"]) < ids.index(sb["id"])            # víc měsíčních subů = výš (Parta měsíce)


def test_member_cap_atomic(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "CapAtomic", "CPA")
    conn = get_conn()
    try:
        conn.execute("UPDATE crews SET member_cap=1 WHERE id=?", (st["id"],)); conn.commit()
    finally:
        conn.close()
    p = _mk_user(100000)
    try:
        _run(crews.join, p, "p", st["code"])                    # cap 1, vůdce už zabírá → plná
        assert False, "atomický cap guard musí odmítnout"
    except ValueError as e:
        assert "plná" in str(e).lower()


def test_leave_cooldown_blocks_join(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "CoolA", "CLA")
    h = _mk_user(100000); sb = _run(crews.create, h, "h", "CoolB", "CLB")
    _run(crews.leave, u)                                        # odejdi → cooldown
    try:
        _run(crews.join, u, "u", sb["code"])                    # join hned → blok
        assert False, "cooldown blokuje join"
    except ValueError as e:
        assert "počkat" in str(e).lower()


def test_private_join_request_approve(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "PrivParta", "PRV")
    _run(crews.toggle_private, h, True)
    p = _mk_user(100000)
    r = _run(crews.join, p, "p", st["code"])
    assert r.get("pending") is True and _member(p) is None      # privátní → žádost, ne člen
    _run(crews.approve_request, h, p)                           # vůdce schválí
    assert _member(p) is not None and _member(p)["crew_id"] == st["id"]


def test_private_join_reject(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "RejParta", "REJ")
    _run(crews.toggle_private, h, True)
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    _run(crews.reject_request, h, p)
    assert _member(p) is None
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM crew_requests WHERE crew_id=? AND user_id=?",
                            (st["id"], p)).fetchone()["c"] == 0
    finally:
        conn.close()


def test_set_motd_leader_only(client):
    u = _mk_user(100000); st = _run(crews.create, u, "u", "MotdParta", "MTD")
    _run(crews.set_motd, u, "Vítejte v naší partě!")
    assert _run(crews.state, u, st["id"])["motd"] == "Vítejte v naší partě!"
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    try:
        _run(crews.set_motd, p, "hack")
        assert False, "jen vůdce mění popis"
    except ValueError:
        pass


def test_achievements_derived(client):
    u = _mk_user(100000); st = _run(crews.create, u, "u", "AchParta", "ACH")
    conn = get_conn()
    try:
        conn.execute("UPDATE crews SET xp=1000000, best_streak=5 WHERE id=?", (st["id"],)); conn.commit()
    finally:
        conn.close()
    names = [a["name"] for a in _run(crews.state, u, st["id"])["achievements"]]
    assert "Lvl 5" in names and "4 týdny v řadě" in names       # lvl6 → Lvl5; streak5 → 4 týdny


def test_set_emblem(client):
    u = _mk_user(100000); st = _run(crews.create, u, "u", "EmblemParta", "EMB")
    cur = _run(crews.state, u, st["id"])["emblem"]
    new_em = next(e for e in crews.EMBLEMS if e != cur)         # jiný než aktuální (hash je per-run náhodný)
    before = _points(u)
    _run(crews.set_emblem, u, new_em)
    conn = get_conn()
    try:
        assert conn.execute("SELECT emblem FROM crews WHERE id=?", (st["id"],)).fetchone()["emblem"] == new_em
    finally:
        conn.close()
    assert _points(u) == before - crews.EMBLEM_COST             # sink
    try:
        _run(crews.set_emblem, u, "XXX")                       # neplatný emblém
        assert False, "neplatný emblém nejde"
    except ValueError:
        pass
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    try:
        _run(crews.set_emblem, p, next(e for e in crews.EMBLEMS if e != new_em))   # nevůdce
        assert False, "jen vůdce mění emblém"
    except ValueError:
        pass


def test_contribute_weekly_reset(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "TydenReset", "TYD")
    conn = get_conn()
    try:
        conn.execute("UPDATE crew_members SET week='2020-W01', week_xp=9999 WHERE user_id=?", (u,))
        conn.commit()
    finally:
        conn.close()
    _commit(crews.contribute, u, 500)
    m = _member(u)
    assert m["week"] == local_week_id() and m["week_xp"] == 500   # nový týden → reset


def test_leaderboard_ranks(client):
    a = _mk_user(100000); sa = _run(crews.create, a, "a", "Top Parta", "TOP")
    b = _mk_user(100000); sb = _run(crews.create, b, "b", "Slabsi Parta", "SLB")
    _commit(crews.contribute, a, 5000)
    _commit(crews.contribute, b, 1000)
    lb = _run(crews.leaderboard, a)
    ids = [c["id"] for c in lb["crews"]]
    assert ids.index(sa["id"]) < ids.index(sb["id"])     # víc týdenního XP = výš


def test_chat_member_only(client):
    h = _mk_user(100000); st = _run(crews.create, h, "host", "ChatParta", "CHT")
    _run(crews.chat_send, h, "host", st["id"], "ahoj parto")
    s = _run(crews.state, h, st["id"])
    assert any(m["msg"] == "ahoj parto" for m in s["chat"])
    out = _mk_user(100000)
    try:
        _run(crews.chat_send, out, "out", st["id"], "nepatrim sem")
        assert False, "nečlen nesmí psát"
    except ValueError:
        pass


# ---------------- týdenní cíl → odměna (výhoda být v partě) ----------------
def _set_week_xp(uid, xp):
    conn = get_conn()
    try:
        conn.execute("UPDATE crew_members SET week_xp=?, week=?, claimed_week=NULL WHERE user_id=?",
                     (xp, local_week_id(), uid))
        conn.commit()
    finally:
        conn.close()


def test_goal_for_scales(client):
    assert crews.goal_for(5) > crews.goal_for(1)              # větší parta = větší cíl


def test_claim_goal_rewards(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "GoalParta", "GOL")
    before = _points(u)
    _set_week_xp(u, crews.goal_for(1) + 100)                  # crew cíl splněn
    out = _run(crews.claim_goal, u)
    assert out["claimed_now"] == crews.GOAL_REWARD
    assert out["you_claimed"] and not out["can_claim_goal"]
    assert _points(u) == before + crews.GOAL_REWARD


def test_claim_goal_tiers_escalate_and_stop(client):
    """Tiery eskalují (GOAL_TIERS ×1/×2/×4/×8); stejný tier 2× nejde, po posledním konec."""
    u = _mk_user(100000); _run(crews.create, u, "u", "GoalTwice", "GO2")
    base = crews.goal_for(1)
    _set_week_xp(u, base + 100)
    assert _run(crews.claim_goal, u)["claimed_now"] == crews.GOAL_TIERS[0][1]   # tier 1
    try:
        _run(crews.claim_goal, u)                                # vyšší tier ještě nesplněný
        assert False, "vyšší tier bez XP nejde"
    except ValueError as e:
        assert "není splněn" in str(e).lower()
    _set_week_xp(u, base * crews.GOAL_TIERS[-1][0] + 100)        # splněné všechny tiery
    for i in range(1, len(crews.GOAL_TIERS)):                    # doclaimuj zbylé tiery po pořádku
        out = _run(crews.claim_goal, u)
        assert out["claimed_now"] == crews.GOAL_TIERS[i][1]
    assert out["goal_all_claimed"] and not out["can_claim_goal"]
    try:
        _run(crews.claim_goal, u)
        assert False, "claim po posledním tieru za týden nejde"
    except ValueError as e:
        assert "vyzvednut" in str(e).lower()


def test_claim_goal_not_reached(client):
    u = _mk_user(100000); _run(crews.create, u, "u", "GoalLow", "GLW")
    _set_week_xp(u, 10)                                       # pod cílem
    try:
        _run(crews.claim_goal, u)
        assert False, "pod cílem nejde vyzvednout"
    except ValueError as e:
        assert "není splněn" in str(e).lower()


def _tag_of(cid):
    conn = get_conn()
    try:
        return conn.execute("SELECT tag FROM crews WHERE id=?", (cid,)).fetchone()["tag"]
    finally:
        conn.close()


def test_tag_any_char_and_optional():
    """Tag je NEPOVINNÝ (prázdný → NULL, víc prázdných koexistuje) a může být COKOLIV (™ apod.)."""
    sfx = secrets.token_hex(3)
    st1 = _run(crews.create, _mk_user(), "u1", f"TmKlub {sfx}", "CRY™")
    assert _tag_of(st1["id"]) == "CRY™", "tag se symbolem ™ se uloží (žádné isalnum)"
    st2 = _run(crews.create, _mk_user(), "u2", f"BeztagA {sfx}", "")
    assert _tag_of(st2["id"]) is None, "prázdný tag → NULL (nepovinný)"
    st3 = _run(crews.create, _mk_user(), "u3", f"BeztagB {sfx}", "")
    assert _tag_of(st3["id"]) is None, "2. prázdný tag → NULL bez UNIQUE kolize"


def test_tag_strips_html():
    """Tag stripne HTML-breakout znaky (defense-in-depth; render stejně escapuje)."""
    sfx = secrets.token_hex(3)
    st = _run(crews.create, _mk_user(), "u", f"Xss {sfx}", "<b>x")
    t = _tag_of(st["id"])
    assert t and "<" not in t and ">" not in t, f"HTML znaky pryč: {t!r}"


def test_admin_list_shows_members():
    """admin_list vrací party + členy (kdo s kým), level/XP."""
    sfx = secrets.token_hex(3)
    st = _run(crews.create, _mk_user(), f"lead_{sfx}", f"AdminView {sfx}", "AV" + sfx[:2])
    rows = _run(crews.admin_list)
    crew = next((c for c in rows if c["id"] == st["id"]), None)
    assert crew is not None, "parta je v admin_list"
    assert crew["member_count"] == 1 and len(crew["members"]) == 1
    assert crew["members"][0]["role"] == "leader", "vůdce mezi členy"
    assert crew["level"] >= 1 and "xp" in crew and "sub_xp" in crew["members"][0]


def test_log_records_lifecycle_events():
    """Historie party zaznamenává založení/vstup/MOTD/roli/kick chronologicky (nejnovější první)."""
    sfx = secrets.token_hex(3)
    leader = _mk_user()
    st = _run(crews.create, leader, f"Leader_{sfx}", f"LogPartyA {sfx}", "LA")
    cid = st["id"]
    member = _mk_user()
    _run(crews.join, member, f"Member_{sfx}", st["code"])
    _run(crews.set_motd, leader, "Ahoj parto!")
    _run(crews.set_role, leader, member, "officer")
    _run(crews.kick, leader, member)
    log = _run(crews.get_log, leader, cid)
    events = [e["event"] for e in log["events"]]
    assert events == ["kicked", "role", "motd", "joined", "created"], f"log pořadí (nejnovější první): {events}"


def test_log_only_visible_to_members():
    """Historii party nesmí číst nečlen (transparentnost dovnitř, ne ven)."""
    sfx = secrets.token_hex(3)
    leader = _mk_user()
    st = _run(crews.create, leader, f"Leader_{sfx}", f"LogPartyB {sfx}", "LB")
    outsider = _mk_user()
    try:
        _run(crews.get_log, outsider, st["id"])
        assert False, "nečlen neměl číst historii"
    except ValueError as e:
        assert "nejsi" in str(e).lower()


def test_war_declare_blocks_duplicate_and_self():
    sfx = secrets.token_hex(3)
    l1 = _mk_user(); st1 = _run(crews.create, l1, f"L1_{sfx}", f"WarA {sfx}", "WA")
    l2 = _mk_user(); st2 = _run(crews.create, l2, f"L2_{sfx}", f"WarB {sfx}", "WB")
    try:
        _run(crews.declare_war, l1, st1["id"])
        assert False, "sám se sebou nelze válčit"
    except ValueError:
        pass
    _run(crews.declare_war, l1, st2["id"])
    try:
        _run(crews.declare_war, l1, st2["id"])
        assert False, "2. válka té samé party měla být odmítnuta"
    except ValueError as e:
        assert "ve válce" in str(e).lower()
    l3 = _mk_user(); st3 = _run(crews.create, l3, f"L3_{sfx}", f"WarC {sfx}", "WC")
    try:
        _run(crews.declare_war, l3, st2["id"])
        assert False, "soupeř už válčí s jiným, mělo selhat"
    except ValueError as e:
        assert "válčí" in str(e).lower()


def test_war_finalize_winner_loser_and_log():
    """Lazy finalizace: delta XP rozhodne vítěze, status-only odměna (war_wins/losses), zalogováno."""
    sfx = secrets.token_hex(3)
    l1 = _mk_user(); st1 = _run(crews.create, l1, f"WL1_{sfx}", f"WinPty {sfx}", "WI")
    l2 = _mk_user(); st2 = _run(crews.create, l2, f"WL2_{sfx}", f"LosePty {sfx}", "LO")
    _run(crews.declare_war, l1, st2["id"])
    _commit(crews.contribute, l1, 5000, False)
    _commit(crews.contribute, l2, 2000, False)
    conn = get_conn()
    try:
        conn.execute("UPDATE crew_wars SET ends_at=? WHERE crew_a_id=?", ("2000-01-01T00:00:00+00:00", st1["id"]))
        conn.commit()
    finally:
        conn.close()
    winner_pub = _run(crews._public, st1["id"], l1)
    loser_pub = _run(crews._public, st2["id"], l2)
    assert winner_pub["war"] is None and loser_pub["war"] is None, "válka skončila → war=None"
    assert winner_pub["war_wins"] == 1 and winner_pub["war_losses"] == 0
    assert loser_pub["war_losses"] == 1 and loser_pub["war_wins"] == 0
    log = _run(crews.get_log, l1, st1["id"])
    assert any(e["event"] == "war_end" and "Vyhráno" in (e["detail"] or "") for e in log["events"])


def test_war_win_rewards_members_capped_weekly(client):
    """Výhra → +WAR_WIN_REWARD každému členovi vítěze (1×/parta/týden). Poražený nic."""
    sfx = secrets.token_hex(3)
    l1 = _mk_user(50000); st1 = _run(crews.create, l1, f"RW1_{sfx}", f"RewWin {sfx}", "")
    m1 = _mk_user(1000); _run(crews.join, m1, f"RWm_{sfx}", st1["code"])   # 2členná vítězná parta
    l2 = _mk_user(50000); st2 = _run(crews.create, l2, f"RW2_{sfx}", f"RewLose {sfx}", "")
    _run(crews.declare_war, l1, st2["id"])
    _commit(crews.contribute, l1, 8000, False)                            # vítěz víc XP
    _commit(crews.contribute, l2, 2000, False)
    bal_l1, bal_m1, bal_l2 = _points(l1), _points(m1), _points(l2)
    conn = get_conn()
    try:
        conn.execute("UPDATE crew_wars SET ends_at='2000-01-01T00:00:00+00:00' WHERE crew_a_id=?", (st1["id"],))
        conn.commit()
    finally:
        conn.close()
    _run(crews._public, st1["id"], l1)                                    # trigger lazy finalize
    assert _points(l1) == bal_l1 + crews.WAR_WIN_REWARD, "vůdce vítěze bere odměnu"
    assert _points(m1) == bal_m1 + crews.WAR_WIN_REWARD, "člen vítěze taky bere"
    assert _points(l2) == bal_l2, "poražený nic nedostane"
    # odměna se NEpočítá do žebříčků (classify zero)
    from app.deps import classify_xp
    assert classify_xp("Crew válka – výhra 🏆")[0] == "zero"


def test_war_finalize_draw():
    sfx = secrets.token_hex(3)
    l1 = _mk_user(); st1 = _run(crews.create, l1, f"D1_{sfx}", f"DrawA {sfx}", "DA")
    l2 = _mk_user(); st2 = _run(crews.create, l2, f"D2_{sfx}", f"DrawB {sfx}", "DB")
    _run(crews.declare_war, l1, st2["id"])
    _commit(crews.contribute, l1, 3000, False)
    _commit(crews.contribute, l2, 3000, False)
    conn = get_conn()
    try:
        conn.execute("UPDATE crew_wars SET ends_at=? WHERE crew_a_id=?", ("2000-01-01T00:00:00+00:00", st1["id"]))
        conn.commit()
    finally:
        conn.close()
    pub1 = _run(crews._public, st1["id"], l1)
    pub2 = _run(crews._public, st2["id"], l2)
    assert pub1["war_draws"] == 1 and pub2["war_draws"] == 1
    assert pub1["war_wins"] == 0 and pub1["war_losses"] == 0


def _crew_xp(uid):
    conn = get_conn()
    try:
        return conn.execute("SELECT c.xp FROM crews c JOIN crew_members m ON m.crew_id=c.id "
                            "WHERE m.user_id=?", (uid,)).fetchone()["xp"]
    finally:
        conn.close()


def test_claim_goal_level_bonus_scales(client):
    """Splněný tier přidá XP do crews.xp (level party) = 5 % z prahu tieru → škáluje s tierem (×1/×2/×4/×8)."""
    u = _mk_user(100000); _run(crews.create, u, "u", "LvlBonus", "LVB")
    base = crews.goal_for(1)
    _set_week_xp(u, base * crews.GOAL_TIERS[-1][0] + 100)          # splněné všechny tiery
    gains = []
    prev = _crew_xp(u)
    for mult, _reward in crews.GOAL_TIERS:
        _run(crews.claim_goal, u)
        now = _crew_xp(u)
        assert now - prev == int(base * mult * crews.GOAL_XP_BONUS_FRAC)   # 5 % z prahu daného tieru
        gains.append(now - prev); prev = now
    assert gains == sorted(gains) and gains[0] < gains[-1]         # roste s tierem (násobí se)


def test_claim_goal_level_bonus_once_per_crew(client):
    """Level bonus = 1× per parta/tier: druhý člen claimující týž tier ho nepřidá znovu (no abuse)."""
    h = _mk_user(100000); st = _run(crews.create, h, "h", "OnceBonus", "ONB")
    p = _mk_user(100000); _run(crews.join, p, "p", st["code"])
    goal = crews.goal_for(2)
    _set_week_xp(h, goal); _set_week_xp(p, goal)                   # součet party ≥ tier 1 cíl
    before = _crew_xp(h)
    _run(crews.claim_goal, h)                                      # 1. člen → bonus
    after_first = _crew_xp(h)
    _run(crews.claim_goal, p)                                      # 2. člen týž tier → BEZ bonusu
    assert after_first - before == int(goal * crews.GOAL_XP_BONUS_FRAC)   # 1× přičteno
    assert _crew_xp(h) == after_first                             # 2. claim nepřidal (gate per parta)


def test_member_activity_visible_only_to_leader():
    """last_active vidí jen vůdce (privacy); ostatní členové NEvidí last_active jiných."""
    sfx = secrets.token_hex(3)
    leader = _mk_user()
    st = _run(crews.create, leader, f"AL_{sfx}", f"ActivityPty {sfx}", "AC")
    member = _mk_user()
    _run(crews.join, member, f"AM_{sfx}", st["code"])
    conn = get_conn()
    try:
        tok = secrets.token_hex(16)
        conn.execute("INSERT INTO sessions (token,user_id,created_at,expires_at,last_seen) VALUES (?,?,?,?,?)",
                    (tok, member, now_iso(), now_iso(), now_iso()))
        conn.commit()
    finally:
        conn.close()
    leader_view = _run(crews._public, st["id"], leader)
    member_view = _run(crews._public, st["id"], member)
    member_row_for_leader = next(m for m in leader_view["members"] if m["user_id"] == member)
    member_row_for_member = next(m for m in member_view["members"] if m["user_id"] == member)
    assert member_row_for_leader["last_active"] is not None, "vůdce vidí last_active"
    assert member_row_for_member["last_active"] is None, "člen NEvidí cizí last_active"
