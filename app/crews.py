"""Crew / Parta (klany) – P1 MVP.

Skupina hráčů co spolu farmí pro společný crew žebříček. 1 hráč = 1 parta.
Crew XP = AGREGACE existující farm aktivity členů (žádný nový faucet) – hook v
economy.award_earned. Per-člen týdenní cap, ať jeden wháál nepotáhne celou partu.
Týdenní reset je lazy (porovnává member.week s aktuálním ISO týdnem).

Sociál: crew chat (vzor jako bj_chat). Vstup: založení = sink sedláků (anti-spam).
"""
import sqlite3
from datetime import datetime, timedelta

from .db import now_iso, local_week_id, local_date
from .deps import try_debit, notify, add_points, XP_PER_SUB, level_info
from .security import new_code

FOUND_COST = 5000         # založení party = sink sedláků (brzdí spam-party)
MEMBER_CAP = 6            # max členů party (2.7. sníženo z 25 — malé těsné party > mega-zergy)
WEEK_XP_CAP = 10000       # max příspěvek 1 člena do crew XP za týden
CHAT_TAIL = 40
EMBLEM_COST = 5000        # změna emblému party = sink sedláků (kosmetika, vůdce only)
LEAVE_COOLDOWN_H = 6      # po odchodu z party musíš počkat X h než vstoupíš jinam (anti-churn/hop)
MOTD_MAX = 200           # max délka crew popisu/MOTD
WAR_HOURS = 24           # Crew War trvá 1 den; status (war_wins/losses/draws) + kořist XP vítězi (níž)
WAR_LOOT_FRAC = 0.5      # vítěz ukořistí 50 % války XP nepřítele → do levelu party (1×/parta/DEN, anti-farm)
EMBLEMS = ["🌾", "🚜", "🐮", "🐔", "🌽", "🥕", "🍺", "⚔️", "🔥", "👑", "🛠️", "🥔",
           "🐺", "🦅", "🐗", "🐉", "🦁", "🐂", "🏰", "🛡️", "🏹", "💀", "⭐", "🍻",
           "🌻", "🐝", "🐴", "🍎", "🪓", "🎯"]

# ── Týdenní crew cíl → odměna všem (hlavní výhoda být v partě) ──
GOAL_BASE = 8000          # základ týdenního crew cíle
GOAL_PER_MEMBER = 3500    # + za každého člena (větší parta = větší cíl)
GOAL_REWARD = 1500        # odměna za tier 1 (základní cíl), per člen
# Eskalace (2.7.): po splnění cíl POKRAČUJE — vyšší meta za menší odměnu, ať má parta co honit
# celý týden. 4. tier ×8 (3.7.) pro silné 6-party, co ×4 přeteklou 1. den. Faucet capnutý:
# max 1500+750+400+250 = 2900/člen/týden.
GOAL_TIERS = [(1, GOAL_REWARD), (2, 750), (4, 400), (8, 250)]   # (násobek základního cíle, odměna/člen)
# Level bonus za splněný tier → XP do crews.xp (level party). 5 % z PRAHU tieru (goal×mult), ne fixní
# faucet: bonus nikdy nepředběhne reálný výkon party. Vyšší tiery jdou jen s reálnými suby (farm je
# capnutý WEEK_XP_CAP), takže velký level boost = jen parta co reálně přivedla $. 1×/tier/parta/týden.
GOAL_XP_BONUS_FRAC = 0.05


def goal_for(members):
    return GOAL_BASE + max(1, members) * GOAL_PER_MEMBER


# Crew SUB-cíl (peer pressure → reálné suby = $ pro streamera). Odměna = STATUS odznak „Supporter parta",
# ne sedláky → žádný faucet. Týdenní počet subů party odvozený z týdenního sub XP (week_xp − week_farm)/5000.
SUB_GOAL_PER_MEMBER = 1     # cílový počet subů/týden na člena
SUB_GOAL_MIN = 2           # minimum (i malá parta má cíl)
# Eskalace sub cíle (2.7.): po splnění další meta = jen VYŠŠÍ STATUS badge — žádné sedláky
# (suby už platí XP + body gifterovi + crew sub bonus; další faucet by se stackoval).
SUB_TIER_MULTS = (1, 2, 4)
SUB_TIER_BADGES = ("Supporter parta ✓", "Supporter parta ×2 🔥", "Supporter parta ×3 👑")


def sub_goal_for(members):
    return max(SUB_GOAL_MIN, members * SUB_GOAL_PER_MEMBER)


CREW_LEVEL_BASE = 12000   # základ level křivky (2.7. laděno na MEMBER_CAP=6)


CAP_FIRST_LVL = 5         # první bonusové místo na lvl 5 (6 → 7)
CAP_LVL_STEP = 10         # pak +1 místo každých 10 levelů → sloty na lvl 5, 15, 25, 35… (řidší = exkluzivní velké party)


def _slot_bonus(level):
    """Kolik bonusových míst má parta na daném levelu. Sloty: 5, 15, 25, 35… (první na 5, pak po 10)."""
    return 0 if level < CAP_FIRST_LVL else (level - CAP_FIRST_LVL) // CAP_LVL_STEP + 1


def cap_for(crew):
    """Efektivní kapacita party: základ (sloupec member_cap) + bonusová místa za level.
    Sloupec zůstává BASE — bonus se počítá dynamicky z crews.xp, takže levelování otvírá
    místa bez update hooků (a level nikdy neklesá → cap taky ne)."""
    return crew["member_cap"] + _slot_bonus(_level(crew["xp"]))


def _level(xp):
    """Crew level z all-time crew XP (sqrt křivka). BEZ stropu (open = trvalá hierarchie,
    vždy jen jedna #1 parta). Základ 12000 (2.7. laděno na MEMBER_CAP=6: plná aktivní parta
    dá ~67k/týden → lvl 5 = 192k ≈ 3 týdny, lvl 10 = 972k ≈ 3,5 měsíce, lvl 21 = 4,8M
    (sub +40 % max) ≈ 1,4 roku = prestige). Bonus stropy viz níže."""
    return 1 + int(((xp or 0) / float(CREW_LEVEL_BASE)) ** 0.5)


# ── Crew level → bonus sedláků (motivace levelovat partu; streamer profituje ze subů) ──
# SUB bonus = juicy (žene subbing = reálný $, NENÍ faucet – body za sub nejdou z farmení).
# FARM bonus = malý (faucet je horký [[prod-econ-anchors]], jen „pocit" perku). Vše tunable.
CREW_SUB_BONUS_PER_LVL = 0.02     # +2 %/lvl na sedláky ze subů/resubů/giftů
CREW_SUB_BONUS_CAP = 0.40         # strop +40 %
CREW_FARM_BONUS_PER_LVL = 0.005   # +0,5 %/lvl na watch/chat/farm
CREW_FARM_BONUS_CAP = 0.05        # strop +5 %


def _bonus_frac(level, kind):
    """Bonusový zlomek (0.06 = +6 %) za crew level. kind='sub' (juicy) / 'farm' (malý)."""
    if kind == "sub":
        return min(CREW_SUB_BONUS_CAP, max(0, level - 1) * CREW_SUB_BONUS_PER_LVL)
    return min(CREW_FARM_BONUS_CAP, max(0, level - 1) * CREW_FARM_BONUS_PER_LVL)


def earn_bonus(conn, uid, kind):
    """Násobitel sedláků z crew levelu (1.0 = bez party = motivace se přidat). kind='sub'/'farm'.
    Volá se z economy.award_earned (farm) a kickevents (_award_kick_user, sub). Levná read cesta."""
    m = _member(conn, uid)
    if not m:
        return 1.0
    c = _crew(conn, m["crew_id"])
    if not c:
        return 1.0
    return 1.0 + _bonus_frac(_level(c["xp"]), kind)


def _prev_week_id():
    """ISO id PŘEDCHOZÍHO týdne (stejný formát jako local_week_id) – kontrola návaznosti streaku."""
    from .db import local_now
    y, w, _ = (local_now() - timedelta(days=7)).isocalendar()
    return f"{y}-W{w:02d}"


def _crew(conn, crew_id):
    return conn.execute("SELECT * FROM crews WHERE id=?", (crew_id,)).fetchone()


def _crew_by_code(conn, code):
    return conn.execute("SELECT * FROM crews WHERE code=?", (code,)).fetchone()


def _member(conn, uid):
    return conn.execute("SELECT * FROM crew_members WHERE user_id=?", (uid,)).fetchone()


def _count(conn, crew_id):
    return conn.execute("SELECT COUNT(*) AS c FROM crew_members WHERE crew_id=?", (crew_id,)).fetchone()["c"]


def _check_cooldown(conn, uid):
    """Anti-churn: po odchodu z party počkej LEAVE_COOLDOWN_H h. Raise když cooldown ještě běží."""
    row = conn.execute("SELECT crew_left_at FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["crew_left_at"]:
        return
    try:
        left = datetime.fromisoformat(row["crew_left_at"])
    except (ValueError, TypeError):
        return
    now = datetime.now(left.tzinfo) if left.tzinfo else datetime.now()
    elapsed_h = (now - left).total_seconds() / 3600
    if elapsed_h < LEAVE_COOLDOWN_H:
        raise ValueError(f"Po odchodu z party musíš počkat {LEAVE_COOLDOWN_H} h (zbývá ~{int(LEAVE_COOLDOWN_H - elapsed_h) + 1} h).")


def _notify_members(conn, crew_id, icon, title, body, link, *, skip=None):
    """Pošli in-app notifikaci všem členům party (kromě `skip`). Necommituje."""
    for r in conn.execute("SELECT user_id FROM crew_members WHERE crew_id=?", (crew_id,)):
        if r["user_id"] != skip:
            notify(conn, r["user_id"], icon, title, body, link)


def _uname(conn, uid):
    r = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    return r["username"] if r else "?"


def _log(conn, crew_id, event, actor_id=None, actor_name=None, target_id=None, target_name=None, detail=""):
    """Zapíš událost do historie party (audit log, read-only pro členy). Necommituje (caller commituje).
    Pozn.: crew_log má ON DELETE CASCADE na crews → historie zaniklé party (poslední odešel) mizí s ní,
    proto se NEVOLÁ při dissolve větvi leave()."""
    conn.execute(
        "INSERT INTO crew_log (crew_id, event, actor_id, actor_name, target_id, target_name, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (crew_id, event, actor_id, actor_name, target_id, target_name, (detail or "")[:200], now_iso()))


def _notify_crew_sub(conn, crew_id, subber_uid, n):
    """Člen subnul → dej partě vědět (sociální posila → reálné suby = $)."""
    row = conn.execute("SELECT username FROM users WHERE id=?", (subber_uid,)).fetchone()
    name = row["username"] if row else "Někdo"
    body = (name + " dal partě " + str(n) + "× sub 🎁") if n > 1 else (name + " subnul pro partu 🎁")
    _notify_members(conn, crew_id, "🎁", "Sub pro partu!", body, "#/crews/" + str(crew_id), skip=subber_uid)


# ---------------- XP hook (volá deps.add_points pro KAŽDÝ kladný XP event) ----------------
def contribute(conn, uid, amount, is_sub=False):
    """Člen vydělal XP → přičti do crew. SUB/resub/gift (is_sub=True) jde UNcapped i týdně
    (supporter MUSÍ zářit – dal reálný prachy), FARM je týdně capnutý (WEEK_XP_CAP, anti-grind).
    Sleduje:
      • week_xp   – týdenní crew XP do žebříčku = farm(cap) + suby(uncapped),
      • week_farm – týdenní FARM akumulátor (jen pro cap, suby ho neplní),
      • contributed – all-time CELKEM přispěno (farm + sub), uncapped,
      • sub_xp    – all-time jen suby (supporter contribution – ten cenný),
      • crews.xp  – all-time crew (level).
    No-op když hráč není v partě. NEcommituje (caller commituje)."""
    amount = max(0, int(amount))
    if amount <= 0:
        return
    m = _member(conn, uid)
    if not m:
        return
    week = local_week_id()
    new_week = m["week"] != week
    cur_xp = 0 if new_week else (m["week_xp"] or 0)         # nový týden → reset obojího
    cur_farm = 0 if new_week else (m["week_farm"] or 0)
    if is_sub:
        wk_add, farm_add = amount, 0                        # suby týdně UNcapped (velký gifter zazáří)
    else:
        farm_add = max(0, min(WEEK_XP_CAP - cur_farm, amount))   # farm týdně capnutý (anti-grind)
        wk_add = farm_add
    conn.execute(
        "UPDATE crew_members SET week_xp=?, week_farm=?, week=?, "
        "contributed=COALESCE(contributed,0)+?, sub_xp=COALESCE(sub_xp,0)+? WHERE user_id=?",
        (cur_xp + wk_add, cur_farm + farm_add, week, amount, amount if is_sub else 0, uid))
    conn.execute("UPDATE crews SET xp = xp + ? WHERE id=?", (amount, m["crew_id"]))  # crew all-time = plné (level)
    if is_sub:
        cur_month = local_date()[:7]                       # YYYY-MM – Parta měsíce (měsíční sub race, lazy reset)
        conn.execute(
            "UPDATE crews SET month_sub_xp = CASE WHEN month=? THEN month_sub_xp + ? ELSE ? END, month=? WHERE id=?",
            (cur_month, amount, amount, cur_month, m["crew_id"]))
        try:                                               # notifikuj partu (sociální posila → víc subů)
            _notify_crew_sub(conn, m["crew_id"], uid, max(1, amount // XP_PER_SUB))
        except Exception:
            pass


# ---------------- akce ----------------
def my_crew(conn, uid):
    m = _member(conn, uid)
    return {"crew_id": m["crew_id"] if m else None}


def _valid_tag(tag):
    """Tag je NEPOVINNÝ a může být COKOLIV (písmena/čísla/symboly ™€🔥…), max 4 znaky.
    Vrací None = bez tagu. Strip jen HTML-breakout + řídicí znaky (render stejně escapuje – defense-in-depth)."""
    import re
    t = re.sub(r"""[<>&"'`\x00-\x1f\x7f]""", "", (tag or "").strip())[:4]
    return t or None


def create(conn, uid, username, name, tag):
    if _member(conn, uid):
        raise ValueError("Už jsi v partě – nejdřív z ní odejdi.")
    _check_cooldown(conn, uid)
    name = (name or "").strip()
    if not (3 <= len(name) <= 32):
        raise ValueError("Název party musí mít 3–32 znaků.")
    t = _valid_tag(tag)   # None = bez tagu (nepovinný)
    if conn.execute("SELECT 1 FROM crews WHERE name=? COLLATE NOCASE", (name,)).fetchone():
        raise ValueError("Parta s tímhle názvem už existuje.")
    if t and conn.execute("SELECT 1 FROM crews WHERE tag=?", (t,)).fetchone():
        raise ValueError("Tenhle tag už někdo má – zkus jiný (nebo nech prázdný).")
    if not try_debit(conn, uid, FOUND_COST, "Založení party 🤝"):
        raise ValueError(f"Na založení party potřebuješ {FOUND_COST} sedláků.")
    code = None
    for _ in range(12):
        c = "P" + new_code()[:6].upper()
        if not _crew_by_code(conn, c):
            code = c
            break
    if not code:
        raise ValueError("Partu se nepodařilo založit, zkus to prosím znovu.")
    emblem = EMBLEMS[abs(hash(t or name)) % len(EMBLEMS)]   # bez tagu → hash z názvu (ať není pořád stejný emblem)
    ts = now_iso()
    try:
        cur = conn.execute(
            "INSERT INTO crews (name, tag, emblem, leader_id, member_cap, code, created_at) VALUES (?,?,?,?,?,?,?)",
            (name, t, emblem, uid, MEMBER_CAP, code, ts))
    except sqlite3.IntegrityError:                     # race: název/tag/kód zabrán mezi checkem a insertem → vrať sink
        add_points(conn, uid, FOUND_COST, "Vrácení – parta nezaložena 🤝", xp=False)
        conn.commit()
        raise ValueError("Parta s tímhle názvem nebo tagem už existuje, zkus jiný.")
    cid = cur.lastrowid
    conn.execute(
        "INSERT INTO crew_members (crew_id, user_id, role, week_xp, week, joined_at) VALUES (?,?,'leader',0,?,?)",
        (cid, uid, local_week_id(), ts))
    _log(conn, cid, "created", uid, username, detail="Parta založena")
    conn.commit()
    return _public(conn, cid, uid)


def _insert_member(conn, crew, uid):
    """Atomický insert člena s cap guardem (fix race check-then-insert). Raise když plno / už člen."""
    try:
        cur = conn.execute(
            "INSERT INTO crew_members (crew_id, user_id, role, week_xp, week, joined_at) "
            "SELECT ?,?,'member',0,?,? WHERE (SELECT COUNT(*) FROM crew_members WHERE crew_id=?) < ?",
            (crew["id"], uid, local_week_id(), now_iso(), crew["id"], cap_for(crew)))
    except sqlite3.IntegrityError:
        raise ValueError("Už jsi v partě.")
    if cur.rowcount == 0:
        raise ValueError("Parta je už plná.")


def _request_join(conn, uid, username, crew):
    """Privátní parta → žádost o vstup místo joinu (vůdce schválí)."""
    if conn.execute("SELECT 1 FROM crew_requests WHERE crew_id=? AND user_id=?", (crew["id"], uid)).fetchone():
        raise ValueError("Žádost už čeká na schválení vůdcem.")
    conn.execute("INSERT INTO crew_requests (crew_id, user_id, username, created_at) VALUES (?,?,?,?)",
                 (crew["id"], uid, username, now_iso()))
    notify(conn, crew["leader_id"], "🔔", "Žádost o vstup do party",
           username + " chce do party " + crew["name"] + ".", "#/crews/" + str(crew["id"]))
    conn.commit()
    return {"pending": True}


def join(conn, uid, username, code):
    if _member(conn, uid):
        raise ValueError("Už jsi v partě – nejdřív z ní odejdi.")
    _check_cooldown(conn, uid)
    crew = _crew_by_code(conn, (code or "").strip().upper())
    if not crew:
        raise ValueError("Taková parta neexistuje – zkontroluj kód.")
    if crew["private"]:                                # privátní → žádost místo přímého joinu
        return _request_join(conn, uid, username, crew)
    _insert_member(conn, crew, uid)                    # atomický cap guard (fix race)
    _notify_members(conn, crew["id"], "🤝", "Nový člen party", username + " se přidal do party!",
                    "#/crews/" + str(crew["id"]), skip=uid)
    _log(conn, crew["id"], "joined", uid, username)
    conn.commit()
    return _public(conn, crew["id"], uid)


def leave(conn, uid):
    m = _member(conn, uid)
    if not m:
        return {"left": True}
    crew_id = m["crew_id"]
    username = _uname(conn, uid)
    conn.execute("DELETE FROM crew_members WHERE user_id=?", (uid,))
    remaining = conn.execute(
        "SELECT user_id FROM crew_members WHERE crew_id=? ORDER BY joined_at, user_id", (crew_id,)).fetchall()
    if not remaining:
        conn.execute("DELETE FROM crews WHERE id=?", (crew_id,))      # poslední odešel → parta zaniká (historie mizí s ní, FK cascade)
    else:
        crew = _crew(conn, crew_id)
        if crew and crew["leader_id"] == uid:                        # vůdce odešel → předej nejstaršímu
            nl = remaining[0]["user_id"]
            conn.execute("UPDATE crews SET leader_id=? WHERE id=?", (nl, crew_id))
            conn.execute("UPDATE crew_members SET role='leader' WHERE crew_id=? AND user_id=?", (crew_id, nl))
            _log(conn, crew_id, "left", uid, username, detail="vůdcovství předáno: " + _uname(conn, nl))
        else:
            _log(conn, crew_id, "left", uid, username)
    conn.execute("UPDATE users SET crew_left_at=? WHERE id=?", (now_iso(), uid))   # cooldown po dobrovolném odchodu
    conn.commit()
    return {"left": True}


def kick(conn, leader_uid, target_uid):
    """Vůdce vyhodí člena. Jen vůdce, ne sebe, jen člena svojí party."""
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew or crew["leader_id"] != leader_uid:
        raise ValueError("Jen vůdce party může vyhazovat.")
    if int(target_uid) == leader_uid:
        raise ValueError("Sebe vyhodit nelze – předej vůdcovství nebo odejdi.")
    tm = _member(conn, target_uid)
    if not tm or tm["crew_id"] != lm["crew_id"]:
        raise ValueError("Tenhle hráč není v tvojí partě.")
    target_name = _uname(conn, target_uid)
    conn.execute("DELETE FROM crew_members WHERE user_id=?", (target_uid,))
    notify(conn, target_uid, "🚪", "Odchod z party", "Byl jsi vyhozen z party " + crew["name"] + ".", "#/crews")
    _log(conn, lm["crew_id"], "kicked", leader_uid, _uname(conn, leader_uid), target_uid, target_name)
    conn.commit()
    return _public(conn, lm["crew_id"], leader_uid)


def set_role(conn, leader_uid, target_uid, role):
    """Vůdce povýší/sundá officera. role: officer | member."""
    if role not in ("officer", "member"):
        raise ValueError("Neplatná role.")
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew or crew["leader_id"] != leader_uid:
        raise ValueError("Jen vůdce party může měnit role.")
    if int(target_uid) == leader_uid:
        raise ValueError("Roli vůdce takhle měnit nelze.")
    tm = _member(conn, target_uid)
    if not tm or tm["crew_id"] != lm["crew_id"]:
        raise ValueError("Tenhle hráč není v tvojí partě.")
    conn.execute("UPDATE crew_members SET role=? WHERE user_id=?", (role, target_uid))
    if role == "officer":
        notify(conn, target_uid, "⭐", "Povýšení v partě",
               "Vůdce tě povýšil na officera party " + crew["name"] + "!", "#/crews/" + str(crew["id"]))
    _log(conn, lm["crew_id"], "role", leader_uid, _uname(conn, leader_uid), target_uid, _uname(conn, target_uid), detail=role)
    conn.commit()
    return _public(conn, lm["crew_id"], leader_uid)


def set_emblem(conn, leader_uid, emblem):
    """Vůdce změní emblém party (z povolené sady). Stojí EMBLEM_COST sedláků (sink, kosmetika)."""
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew or crew["leader_id"] != leader_uid:
        raise ValueError("Jen vůdce party může měnit emblém.")
    if emblem not in EMBLEMS:
        raise ValueError("Neplatný emblém.")
    if emblem == crew["emblem"]:
        return _public(conn, lm["crew_id"], leader_uid)             # beze změny → neúčtuj
    if not try_debit(conn, leader_uid, EMBLEM_COST, "Změna emblému party 🎨"):
        raise ValueError(f"Na změnu emblému potřebuješ {EMBLEM_COST} sedláků.")
    conn.execute("UPDATE crews SET emblem=? WHERE id=?", (emblem, lm["crew_id"]))
    _log(conn, lm["crew_id"], "emblem", leader_uid, _uname(conn, leader_uid), detail=emblem)
    conn.commit()
    return _public(conn, lm["crew_id"], leader_uid)


def toggle_private(conn, leader_uid, private):
    """Vůdce zapne/vypne privátní režim (join = žádost ke schválení)."""
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew or crew["leader_id"] != leader_uid:
        raise ValueError("Jen vůdce mění soukromí party.")
    conn.execute("UPDATE crews SET private=? WHERE id=?", (1 if private else 0, lm["crew_id"]))
    _log(conn, lm["crew_id"], "private", leader_uid, _uname(conn, leader_uid), detail="zapnuto 🔒" if private else "vypnuto 🔓")
    conn.commit()
    return _public(conn, lm["crew_id"], leader_uid)


def set_motd(conn, leader_uid, text):
    """Vůdce nastaví popis/MOTD party (max MOTD_MAX znaků)."""
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew or crew["leader_id"] != leader_uid:
        raise ValueError("Jen vůdce mění popis party.")
    text = (text or "").strip()[:MOTD_MAX]
    conn.execute("UPDATE crews SET motd=? WHERE id=?", (text, lm["crew_id"]))
    _log(conn, lm["crew_id"], "motd", leader_uid, _uname(conn, leader_uid), detail=text[:60] or "(smazáno)")
    conn.commit()
    return _public(conn, lm["crew_id"], leader_uid)


def approve_request(conn, leader_uid, target_uid):
    """Vůdce schválí žádost o vstup (přidá člena, respektuje cap atomicky)."""
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew or crew["leader_id"] != leader_uid:
        raise ValueError("Jen vůdce schvaluje žádosti.")
    req = conn.execute("SELECT username FROM crew_requests WHERE crew_id=? AND user_id=?",
                       (crew["id"], target_uid)).fetchone()
    if not req:
        raise ValueError("Žádost neexistuje.")
    if _member(conn, target_uid):                      # mezitím už někam vstoupil → zahoď žádost
        conn.execute("DELETE FROM crew_requests WHERE crew_id=? AND user_id=?", (crew["id"], target_uid))
        conn.commit()
        raise ValueError("Hráč už je v nějaké partě.")
    _insert_member(conn, crew, target_uid)             # atomický cap guard
    conn.execute("DELETE FROM crew_requests WHERE crew_id=? AND user_id=?", (crew["id"], target_uid))
    notify(conn, target_uid, "✅", "Přijat do party",
           "Vůdce tě přijal do party " + crew["name"] + "!", "#/crews/" + str(crew["id"]))
    _notify_members(conn, crew["id"], "🤝", "Nový člen party", req["username"] + " se přidal do party!",
                    "#/crews/" + str(crew["id"]), skip=target_uid)
    _log(conn, crew["id"], "joined", target_uid, req["username"], detail="schváleno vůdcem")
    conn.commit()
    return _public(conn, crew["id"], leader_uid)


def reject_request(conn, leader_uid, target_uid):
    """Vůdce zamítne žádost o vstup."""
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew or crew["leader_id"] != leader_uid:
        raise ValueError("Jen vůdce schvaluje žádosti.")
    conn.execute("DELETE FROM crew_requests WHERE crew_id=? AND user_id=?", (crew["id"], target_uid))
    notify(conn, target_uid, "🚫", "Žádost zamítnuta",
           "Vůdce zamítl tvou žádost do party " + crew["name"] + ".", "#/crews")
    conn.commit()
    return _public(conn, crew["id"], leader_uid)


def chat_send(conn, uid, username, crew_id, msg):
    m = _member(conn, uid)
    if not m or m["crew_id"] != crew_id:
        raise ValueError("Nejsi v téhle partě.")
    msg = (msg or "").strip()[:200]
    if msg:
        conn.execute("INSERT INTO crew_chat (crew_id, user_id, username, msg, created_at) VALUES (?,?,?,?,?)",
                     (crew_id, uid, username, msg, now_iso()))
        conn.commit()
    return {"ok": True}


def claim_goal(conn, uid):
    """Člen vyzvedne odměnu za splněný TIER týdenního cíle. Tiery eskalují (GOAL_TIERS:
    1×/2×/4× základ), každý tier max 1×/týden/USER, claimují se popořadě.
    Gate je na users.crew_goal_week + crew_goal_tier (NE na member řádku) → leave+rejoin
    neobejde (hop-proof); po hopu jde claimnout jen VYŠŠÍ tier, a jen když ho nová parta má."""
    m = _member(conn, uid)
    if not m:
        raise ValueError("Nejsi v žádné partě.")
    week = local_week_id()
    ur = conn.execute("SELECT crew_goal_week, crew_goal_tier FROM users WHERE id=?", (uid,)).fetchone()
    done = (ur["crew_goal_tier"] or 0) if (ur and ur["crew_goal_week"] == week) else 0
    if done >= len(GOAL_TIERS):
        raise ValueError("Všechny tiery týdenního cíle už máš vyzvednuté. 🎁 V pondělí nanovo!")
    crew_id = m["crew_id"]
    week_xp = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN week=? THEN week_xp ELSE 0 END),0) AS x "
        "FROM crew_members WHERE crew_id=?", (week, crew_id)).fetchone()["x"]
    mult, reward = GOAL_TIERS[done]
    if week_xp < goal_for(_count(conn, crew_id)) * mult:
        raise ValueError(f"Tier {done + 1} týdenního cíle ještě není splněný. Farmařte spolu! 🌾")
    # atomický gate na USERA – každý tier max 1×/týden bez ohledu na hopování part
    if done == 0:
        ok = conn.execute(
            "UPDATE users SET crew_goal_week=?, crew_goal_tier=1 WHERE id=? "
            "AND (crew_goal_week IS NULL OR crew_goal_week<>?)", (week, uid, week)).rowcount
    else:
        ok = conn.execute(
            "UPDATE users SET crew_goal_tier=? WHERE id=? AND crew_goal_week=? AND crew_goal_tier=?",
            (done + 1, uid, week, done)).rowcount
    if not ok:
        raise ValueError("Tuhle odměnu už máš vyzvednutou. 🎁")
    conn.execute("UPDATE crew_members SET claimed_week=? WHERE user_id=?", (week, uid))   # display konzistence
    crew = _crew(conn, crew_id)                       # crew streak: 1×/týden při prvním claimu (cíl je splněný)
    if crew["streak_week"] != week:
        ns = (crew["streak"] + 1) if crew["streak_week"] == _prev_week_id() else 1
        conn.execute("UPDATE crews SET streak=?, streak_week=?, best_streak=MAX(best_streak, ?) WHERE id=?",
                     (ns, week, ns, crew_id))
    label = "Crew týdenní cíl 🤝" if done == 0 else f"Crew týdenní cíl – tier {done + 1} 🤝"
    add_points(conn, uid, reward, label, xp=False)
    _log(conn, crew_id, "goal", uid, _uname(conn, uid),
         detail=f"+{reward} sedláků (týdenní cíl{'' if done == 0 else f' tier {done + 1}'})")
    # Level bonus do crews.xp: 1× per tier per parta/týden (atomický crew gate → hop ani víc členů claimujících
    # týž tier ho nezmnoží). Bonus = zlomek prahu tieru → neabusovatelné (vyšší tiery = jen s reálnými suby).
    xp_bonus = int(goal_for(_count(conn, crew_id)) * mult * GOAL_XP_BONUS_FRAC)
    if xp_bonus > 0 and conn.execute(
            "UPDATE crews SET goal_bonus_tier=?, goal_bonus_week=? WHERE id=? "
            "AND (goal_bonus_week IS NULL OR goal_bonus_week<>? OR goal_bonus_tier<?)",
            (done + 1, week, crew_id, week, done + 1)).rowcount:
        conn.execute("UPDATE crews SET xp = xp + ? WHERE id=?", (xp_bonus, crew_id))
        _log(conn, crew_id, "goal_xp", uid, _uname(conn, uid),
             detail=f"+{xp_bonus} XP partě do levelu (tier {done + 1})")
    conn.commit()
    out = _public(conn, crew_id, uid)
    out["claimed_now"] = reward
    return out


# ---------------- Crew Wars ----------------
# Týdenní/víkendová drama mezi 2 partama: skóre = delta crews.xp od vyhlášení do konce (start_xp snapshot).
# Odměna = status (war_wins/losses/draws) + KOŘIST: vítěz ukořistí 50 % XP co nepřítel za válku nafarmil,
# do svého crews.xp (level). Kopie – poražený o nic nepřijde. Lazy finalizace (jako aukce _finalize_expired).
def _pay_war_loot(conn, winner_id, loser_delta):
    """Kořist za VÝHRU: vítěz +WAR_LOOT_FRAC × (XP co nepřítel za válku nafarmil) do crews.xp (level party).
    Kopie – poražený si své XP nechá. Max 1×/parta/DEN (válka trvá 1 den → každá denní válka může platit;
    strop proti spamu). Jen reálná výhra (ne dissolve-win → zavírá abusy typu vytvoř+rozpusť spojence).
    Vrací připsanou kořist (0 = nic). Necommituje."""
    loot = int(round(max(0, loser_delta) * WAR_LOOT_FRAC))
    if loot <= 0:
        return 0
    day = local_date()   # denní gate (sloupec war_reward_week teď drží DATUM, ne ISO týden) – 1 den = 1 válka
    # atomický denní gate na partě → i souběh finalizací odmění max 1×/den
    if conn.execute("UPDATE crews SET war_reward_week=? WHERE id=? AND (war_reward_week IS NULL OR war_reward_week<>?)",
                    (day, winner_id, day)).rowcount != 1:
        return 0
    conn.execute("UPDATE crews SET xp = xp + ? WHERE id=?", (loot, winner_id))
    return loot


def _finalize_wars(conn):
    """Uzavře vypršené aktivní války: spočítá deltu XP obou stran, zapíše vítěze (None=remíza),
    připíše war_wins/losses/draws, zaloguje do historie obou párty + notifikuje členy. Necommituje
    (caller commituje, stejně jako auctions._finalize_expired)."""
    now = now_iso()
    rows = conn.execute("SELECT * FROM crew_wars WHERE status='active' AND ends_at<=?", (now,)).fetchall()
    if not rows:
        return
    for w in rows:
        if conn.execute("UPDATE crew_wars SET status='ended' WHERE id=? AND status='active'",
                        (w["id"],)).rowcount != 1:
            continue   # souběh – jiný request to už uzavřel
        ca, cb = _crew(conn, w["crew_a_id"]), _crew(conn, w["crew_b_id"])
        if not ca and not cb:                     # obě party mezitím zanikly → bez vítěze, nic k zalogování
            continue
        if ca and not cb:                          # soupeř zanikl → automatická výhra
            conn.execute("UPDATE crew_wars SET winner_crew_id=? WHERE id=?", (ca["id"], w["id"]))
            conn.execute("UPDATE crews SET war_wins=war_wins+1 WHERE id=?", (ca["id"],))
            _log(conn, ca["id"], "war_end", detail="Vyhráno – soupeř zanikl 🏆")
            _notify_members(conn, ca["id"], "🏆", "Vyhráli jste válku!", "Soupeř zanikl – výhra automaticky.", "#/crews/" + str(ca["id"]))
            continue
        if cb and not ca:
            conn.execute("UPDATE crew_wars SET winner_crew_id=? WHERE id=?", (cb["id"], w["id"]))
            conn.execute("UPDATE crews SET war_wins=war_wins+1 WHERE id=?", (cb["id"],))
            _log(conn, cb["id"], "war_end", detail="Vyhráno – soupeř zanikl 🏆")
            _notify_members(conn, cb["id"], "🏆", "Vyhráli jste válku!", "Soupeř zanikl – výhra automaticky.", "#/crews/" + str(cb["id"]))
            continue
        delta_a = max(0, (ca["xp"] or 0) - w["start_xp_a"])
        delta_b = max(0, (cb["xp"] or 0) - w["start_xp_b"])
        if delta_a == delta_b:
            conn.execute("UPDATE crews SET war_draws=war_draws+1 WHERE id IN (?,?)", (ca["id"], cb["id"]))
            for c, opp, mine in ((ca, cb, delta_a), (cb, ca, delta_b)):
                _log(conn, c["id"], "war_end", detail=f"Remíza proti {opp['name']} ({mine} XP : {mine} XP)")
                _notify_members(conn, c["id"], "🤝", "Válka skončila remízou", f"Remíza proti {opp['name']}! Oba {mine} XP.", "#/crews/" + str(c["id"]))
        else:
            winner, loser = (ca, cb) if delta_a > delta_b else (cb, ca)
            wd, ld = (delta_a, delta_b) if delta_a > delta_b else (delta_b, delta_a)
            conn.execute("UPDATE crew_wars SET winner_crew_id=? WHERE id=?", (winner["id"], w["id"]))
            conn.execute("UPDATE crews SET war_wins=war_wins+1 WHERE id=?", (winner["id"],))
            conn.execute("UPDATE crews SET war_losses=war_losses+1 WHERE id=?", (loser["id"],))
            loot = _pay_war_loot(conn, winner["id"], ld)     # kořist: 50 % XP nepřítele do levelu (1×/parta/den)
            bonus_txt = f" Ukořistili jste +{loot} XP z nepřítele → level party ⬆️" if loot else ""
            _log(conn, winner["id"], "war_end", detail=f"Vyhráno proti {loser['name']} ({wd} : {ld} XP) 🏆{f' +{loot} XP kořist' if loot else ''}")
            _log(conn, loser["id"], "war_end", detail=f"Prohráno proti {winner['name']} ({ld} : {wd} XP)")
            _notify_members(conn, winner["id"], "🏆", "Vyhráli jste válku!", f"Porazili jste {loser['name']} ({wd} : {ld} XP)!{bonus_txt}", "#/crews/" + str(winner["id"]))
            _notify_members(conn, loser["id"], "💀", "Prohráli jste válku", f"{winner['name']} vás porazili ({ld} : {wd} XP). Příště!", "#/crews/" + str(loser["id"]))


def declare_war(conn, leader_uid, opponent_crew_id):
    """Vůdce NEBO důstojník vyhlásí válku jiné partě. 1 aktivní válka/parta (přirozený throttle,
    žádné stohování). Odměna = status + kořist 50 % XP nepřítele vítězi (viz _pay_war_loot)."""
    _finalize_wars(conn)
    lm = _member(conn, leader_uid)
    if not lm:
        raise ValueError("Nejsi v partě.")
    crew = _crew(conn, lm["crew_id"])
    if not crew:
        raise ValueError("Nejsi v partě.")
    if lm["role"] not in ("leader", "officer"):
        raise ValueError("Válku může vyhlásit jen vůdce nebo důstojník party.")
    opponent_crew_id = int(opponent_crew_id)
    if opponent_crew_id == crew["id"]:
        raise ValueError("Sám se sebou válčit nelze.")
    opp = _crew(conn, opponent_crew_id)
    if not opp:
        raise ValueError("Tahle parta neexistuje.")
    if conn.execute("SELECT 1 FROM crew_wars WHERE (crew_a_id=? OR crew_b_id=?) AND status='active'",
                    (crew["id"], crew["id"])).fetchone():
        raise ValueError("Tvoje parta už ve válce je – počkej až skončí.")
    if conn.execute("SELECT 1 FROM crew_wars WHERE (crew_a_id=? OR crew_b_id=?) AND status='active'",
                    (opp["id"], opp["id"])).fetchone():
        raise ValueError(f"{opp['name']} už s někým válčí – zkus jinou partu.")
    ts = now_iso()
    ends = (datetime.fromisoformat(ts) + timedelta(hours=WAR_HOURS)).isoformat()
    conn.execute(
        "INSERT INTO crew_wars (crew_a_id, crew_b_id, start_xp_a, start_xp_b, started_at, ends_at) VALUES (?,?,?,?,?,?)",
        (crew["id"], opp["id"], crew["xp"] or 0, opp["xp"] or 0, ts, ends))
    _log(conn, crew["id"], "war_start", leader_uid, _uname(conn, leader_uid), detail=f"vs {opp['name']}")
    _log(conn, opp["id"], "war_start", leader_uid, _uname(conn, leader_uid), detail=f"vs {crew['name']}")
    _notify_members(conn, crew["id"], "⚔️", "Vyhlásili jste válku!", f"Válka proti {opp['name']} začíná – kdo nasbírá víc XP do {WAR_HOURS}h, vyhrává!", "#/crews/" + str(crew["id"]))
    _notify_members(conn, opp["id"], "⚔️", "Vyhlásili vám válku!", f"{crew['name']} vám vyhlásili válku – braňte se, sbírejte XP! ({WAR_HOURS}h)", "#/crews/" + str(opp["id"]))
    conn.commit()
    return _public(conn, crew["id"], leader_uid)


def _war_state(conn, crew):
    """Aktivní válka party (pro _public detail) – soupeř, kdo aktuálně vede, kdy končí. None = bez války."""
    w = conn.execute(
        "SELECT * FROM crew_wars WHERE (crew_a_id=? OR crew_b_id=?) AND status='active'",
        (crew["id"], crew["id"])).fetchone()
    if not w:
        return None
    am_a = w["crew_a_id"] == crew["id"]
    opp_id = w["crew_b_id"] if am_a else w["crew_a_id"]
    opp = _crew(conn, opp_id)
    if not opp:
        return None
    my_gain = max(0, (crew["xp"] or 0) - (w["start_xp_a"] if am_a else w["start_xp_b"]))
    opp_gain = max(0, (opp["xp"] or 0) - (w["start_xp_b"] if am_a else w["start_xp_a"]))
    return {"opponent_id": opp["id"], "opponent_name": opp["name"], "opponent_tag": opp["tag"],
            "opponent_emblem": opp["emblem"], "my_gain": my_gain, "opp_gain": opp_gain,
            "started_at": w["started_at"], "ends_at": w["ends_at"]}


# ---------------- veřejný stav ----------------
def leaderboard(conn, uid, limit=50, sort="week"):
    """sort='week' → týdenní XP (farm+sub). 'subs' → SUPPORTER board (all-time sub_xp party).
    'month' → PARTA MĚSÍCE (měsíční sub race, #1 = koruna; měsíčně se resetuje → vždy nová soutěž)."""
    _finalize_wars(conn)
    conn.commit()
    week = local_week_id()
    month = local_date()[:7]
    order = {"subs": "sub_total DESC, c.id ASC",
             "month": "month_xp DESC, c.id ASC"}.get(sort, "week_xp DESC, c.id ASC")
    rows = conn.execute(
        "SELECT c.id, c.name, c.tag, c.emblem, c.xp, "
        "(CASE WHEN c.month=? THEN c.month_sub_xp ELSE 0 END) AS month_xp, "
        "(SELECT COUNT(*) FROM crew_members m WHERE m.crew_id=c.id) AS members, "
        "(SELECT COALESCE(SUM(CASE WHEN m.week=? THEN m.week_xp ELSE 0 END),0) "
        " FROM crew_members m WHERE m.crew_id=c.id) AS week_xp, "
        "(SELECT COALESCE(SUM(m.sub_xp),0) FROM crew_members m WHERE m.crew_id=c.id) AS sub_total "
        "FROM crews c ORDER BY " + order + " LIMIT ?",
        (month, week, limit)).fetchall()
    m = _member(conn, uid)
    at_war = {r["crew_a_id"] for r in conn.execute("SELECT crew_a_id FROM crew_wars WHERE status='active'")} \
        | {r["crew_b_id"] for r in conn.execute("SELECT crew_b_id FROM crew_wars WHERE status='active'")}
    return {"week": week, "month": month, "sort": sort, "my_crew_id": m["crew_id"] if m else None,
            "crews": [{"rank": i + 1, "id": r["id"], "name": r["name"], "tag": r["tag"],
                       "emblem": r["emblem"], "members": r["members"], "week_xp": r["week_xp"],
                       "sub_total": r["sub_total"], "month_xp": r["month_xp"],
                       "level": _level(r["xp"]), "goal": goal_for(r["members"]), "at_war": r["id"] in at_war}
                      for i, r in enumerate(rows)]}


def tags(conn):
    """Mapa username_lower → TAG pro VŠECHNY členy crew (pro [TAG] u nicku globálně, cache na frontu)."""
    rows = conn.execute(
        "SELECT LOWER(u.username) AS un, c.tag AS tag FROM crew_members m "
        "JOIN users u ON u.id = m.user_id JOIN crews c ON c.id = m.crew_id").fetchall()
    return {r["un"]: r["tag"] for r in rows}


def state(conn, uid, crew_id):
    return _public(conn, crew_id, uid)


_LOG_VERBS = {
    "created": "🤝 založil(a) partu", "joined": "🤝 vstoupil(a) do party", "left": "🚪 opustil(a) partu",
    "kicked": "👢 vyhodil(a)", "role": "⭐ změnil(a) roli", "emblem": "🎨 změnil(a) emblém",
    "motd": "📝 upravil(a) popis", "private": "🔒 změnil(a) soukromí", "goal": "🎁 vyzvedl(a) odměnu",
    "war_start": "⚔️ vyhlásil(a) válku", "war_end": "🏆 válka skončila",
}


def get_log(conn, uid, crew_id, limit=50):
    """Historie party (audit log) – vidí JEN členové (transparentnost dovnitř, ne ven)."""
    m = _member(conn, uid)
    if not m or m["crew_id"] != crew_id:
        raise ValueError("Nejsi v téhle partě.")
    rows = conn.execute(
        "SELECT event, actor_id, actor_name, target_id, target_name, detail, created_at FROM crew_log "
        "WHERE crew_id=? ORDER BY id DESC LIMIT ?", (crew_id, min(200, max(1, limit)))).fetchall()
    return {"events": [{"event": r["event"], "verb": _LOG_VERBS.get(r["event"], r["event"]),
                        "actor_name": r["actor_name"], "target_name": r["target_name"],
                        "detail": r["detail"] or "", "created_at": r["created_at"]} for r in rows]}


def admin_list(conn):
    """VŠECHNY party pro admina: meta (level/XP/streak/MOTD/soukromá/kód) + členové (kdo s kým, role,
    all-time příspěvek, sub XP, týdenní XP). Vůdce první, pak dle příspěvku."""
    _finalize_wars(conn)
    conn.commit()
    out = []
    for c in conn.execute("SELECT * FROM crews ORDER BY xp DESC, id DESC"):
        members = []
        for m in conn.execute(
            "SELECT m.user_id, m.role, m.contributed, m.sub_xp, m.week_xp, m.joined_at, "
            "u.username, u.kick_username FROM crew_members m JOIN users u ON u.id = m.user_id "
            "WHERE m.crew_id = ? ORDER BY (m.role = 'leader') DESC, m.contributed DESC", (c["id"],)):
            members.append({"user_id": m["user_id"], "username": m["username"], "kick_username": m["kick_username"],
                            "role": m["role"], "contributed": m["contributed"] or 0, "sub_xp": m["sub_xp"] or 0,
                            "week_xp": m["week_xp"] or 0, "joined_at": m["joined_at"]})
        out.append({"id": c["id"], "name": c["name"], "tag": c["tag"] or "", "emblem": c["emblem"],
                    "level": _level(c["xp"]), "xp": c["xp"] or 0, "member_count": len(members),
                    "member_cap": cap_for(c), "private": bool(c["private"]), "streak": c["streak"] or 0,
                    "best_streak": c["best_streak"] or 0, "motd": c["motd"] or "", "code": c["code"],
                    "war_wins": c["war_wins"] or 0, "war_losses": c["war_losses"] or 0, "war_draws": c["war_draws"] or 0,
                    "created_at": c["created_at"], "members": members})
    return out


def _achievements(c, members_count, sub_total):
    """Odvozené odznaky party z aktuálních statů (počítá se on-the-fly, bez úložiště)."""
    lvl = _level(c["xp"])
    best = c["best_streak"] or 0
    defs = [
        (lvl >= 5, "🌱", "Lvl 5"),
        (lvl >= 10, "🌳", "Lvl 10"),
        (lvl >= 21, "👑", "Lvl 21 — maxbonus"),
        (sub_total >= 50 * XP_PER_SUB, "🎁", "50 subů"),
        (sub_total >= 200 * XP_PER_SUB, "🏆", "200 subů"),
        (best >= 4, "🔥", "4 týdny v řadě"),
        (best >= 12, "💎", "12 týdnů v řadě"),
        (members_count >= cap_for(c), "👥", "Plná parta"),
    ]
    return [{"icon": ic, "name": nm} for ok, ic, nm in defs if ok]


def _public(conn, crew_id, viewer_uid):
    _finalize_wars(conn)
    conn.commit()
    c = _crew(conn, crew_id)
    if not c:
        return None
    week = local_week_id()
    is_leader = c["leader_id"] == viewer_uid
    # Aktivita členů (poslední přihlášení) – jen vůdce vidí (pomáhá rozhodnout koho kicknout). 1 batch query, ne N+1.
    last_active = {}
    if is_leader:
        last_active = {r["user_id"]: r["ls"] for r in conn.execute(
            "SELECT user_id, MAX(last_seen) AS ls FROM sessions WHERE user_id IN "
            "(SELECT user_id FROM crew_members WHERE crew_id=?) GROUP BY user_id", (crew_id,))}
    members, total_week, week_sub_xp, total_sub = [], 0, 0, 0
    you_member = False
    you_claimed_week = None
    for m in conn.execute(
        "SELECT m.user_id, m.role, m.week_xp, m.week_farm, m.week, m.claimed_week, m.contributed, m.sub_xp, u.username, u.avatar_url FROM crew_members m "
        "JOIN users u ON u.id=m.user_id WHERE m.crew_id=? ORDER BY m.contributed DESC, (m.week=?) DESC, m.week_xp DESC, m.joined_at",
        (crew_id, week)):
        same_week = m["week"] == week
        wx = m["week_xp"] if same_week else 0
        wf = (m["week_farm"] or 0) if same_week else 0
        total_week += wx
        week_sub_xp += max(0, wx - wf)                  # týdenní sub XP člena = week_xp − week_farm
        is_you = m["user_id"] == viewer_uid
        you_member = you_member or is_you
        if is_you:
            you_claimed_week = m["claimed_week"]
        sx = m["sub_xp"] or 0
        total_sub += sx
        members.append({"user_id": m["user_id"], "username": m["username"],
                        "avatar_url": m["avatar_url"] or "", "role": m["role"],
                        "last_active": last_active.get(m["user_id"]) if is_leader else None,
                        "week_xp": wx, "contributed": m["contributed"] or 0,
                        "sub_xp": sx, "farm_xp": max(0, (m["contributed"] or 0) - sx), "is_you": is_you})
    chat = []
    if you_member:
        chat = [dict(r) for r in conn.execute(
            "SELECT username, msg, created_at FROM crew_chat WHERE crew_id=? ORDER BY id DESC LIMIT ?",
            (crew_id, CHAT_TAIL)).fetchall()][::-1]
    goal = goal_for(len(members))
    you_tier_done = 0
    if you_member:                                  # claim evidovaný na USERA (hop-proof)
        _ur = conn.execute("SELECT crew_goal_week, crew_goal_tier FROM users WHERE id=?", (viewer_uid,)).fetchone()
        you_tier_done = (_ur["crew_goal_tier"] or 0) if (_ur and _ur["crew_goal_week"] == week) else 0
    tiers_n = len(GOAL_TIERS)
    all_claimed = you_tier_done >= tiers_n
    cur_mult, cur_reward = GOAL_TIERS[min(you_tier_done, tiers_n - 1)]
    cur_goal = goal * cur_mult                      # cíl AKTUÁLNÍHO tieru diváka (bar míří na něj)
    goal_reached = total_week >= cur_goal
    lvl = _level(c["xp"])
    _lp = level_info(c["xp"], CREW_LEVEL_BASE)   # progress pro level bar (kolik XP do dalšího lvl)
    week_subs = week_sub_xp // XP_PER_SUB
    sub_goal_base = sub_goal_for(len(members))
    sub_tier = sum(1 for mlt in SUB_TIER_MULTS if week_subs >= sub_goal_base * mlt)
    # bar míří na další nesplněnou metu (maxnutá parta vidí poslední)
    sub_goal = sub_goal_base * SUB_TIER_MULTS[min(sub_tier, len(SUB_TIER_MULTS) - 1)]
    streak = c["streak"] if c["streak_week"] in (week, _prev_week_id()) else 0   # živý jen pokud tento/minulý týden
    requests = []
    if is_leader:                                   # žádosti o vstup vidí jen vůdce
        requests = [dict(r) for r in conn.execute(
            "SELECT user_id, username, created_at FROM crew_requests WHERE crew_id=? ORDER BY created_at",
            (crew_id,)).fetchall()]
    return {
        "id": c["id"], "name": c["name"], "tag": c["tag"], "emblem": c["emblem"],
        "leader_id": c["leader_id"], "member_cap": cap_for(c), "members_count": len(members),
        "next_slot_level": CAP_FIRST_LVL + max(0, lvl - CAP_FIRST_LVL + CAP_LVL_STEP) // CAP_LVL_STEP * CAP_LVL_STEP,   # další slot: 5,15,25…
        "xp": c["xp"], "level": lvl,
        "level_into": _lp["into"], "level_span": _lp["span"], "level_pct": _lp["pct"],
        "sub_bonus_pct": round(_bonus_frac(lvl, "sub") * 100),
        "farm_bonus_pct": round(_bonus_frac(lvl, "farm") * 100, 1),
        "week_xp": total_week, "week": week,
        "goal": cur_goal, "goal_base": goal, "goal_reached": goal_reached, "goal_reward": cur_reward,
        "goal_xp_bonus": int(cur_goal * GOAL_XP_BONUS_FRAC),   # XP do levelu party za claim aktuálního tieru (1×/parta)
        "goal_tier": min(you_tier_done + 1, tiers_n), "goal_tiers_total": tiers_n,
        "goal_all_claimed": all_claimed,
        "you_claimed": you_tier_done > 0,
        "can_claim_goal": bool(you_member and goal_reached and not all_claimed),
        "week_subs": week_subs, "sub_goal": sub_goal, "sub_goal_reached": sub_tier >= 1,
        "sub_tier": sub_tier, "sub_tiers_total": len(SUB_TIER_MULTS),
        "sub_badge": SUB_TIER_BADGES[sub_tier - 1] if sub_tier else None,
        "streak": streak, "best_streak": c["best_streak"] or 0,
        "motd": c["motd"] or "", "private": bool(c["private"]),
        "achievements": _achievements(c, len(members), total_sub),
        "requests": requests,
        "code": c["code"] if you_member else None,        # kód vidí jen členové
        "members": members, "chat": chat,
        "is_member": you_member, "is_leader": is_leader,
        "war": _war_state(conn, c), "war_wins": c["war_wins"] or 0,
        "war_losses": c["war_losses"] or 0, "war_draws": c["war_draws"] or 0,
    }
