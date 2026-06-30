"""Crew / Parta (klany) – P1 MVP.

Skupina hráčů co spolu farmí pro společný crew žebříček. 1 hráč = 1 parta.
Crew XP = AGREGACE existující farm aktivity členů (žádný nový faucet) – hook v
economy.award_earned. Per-člen týdenní cap, ať jeden wháál nepotáhne celou partu.
Týdenní reset je lazy (porovnává member.week s aktuálním ISO týdnem).

Sociál: crew chat (vzor jako bj_chat). Vstup: založení = sink sedláků (anti-spam).
"""
import sqlite3
from datetime import datetime

from .db import now_iso, local_week_id, local_date
from .deps import try_debit, notify, add_points, XP_PER_SUB
from .security import new_code

FOUND_COST = 25000        # založení party = sink sedláků (brzdí spam-party)
MEMBER_CAP = 25
WEEK_XP_CAP = 10000       # max příspěvek 1 člena do crew XP za týden
CHAT_TAIL = 40
EMBLEM_COST = 5000        # změna emblému party = sink sedláků (kosmetika, vůdce only)
LEAVE_COOLDOWN_H = 6      # po odchodu z party musíš počkat X h než vstoupíš jinam (anti-churn/hop)
MOTD_MAX = 200           # max délka crew popisu/MOTD
EMBLEMS = ["🌾", "🚜", "🐮", "🐔", "🌽", "🥕", "🍺", "⚔️", "🔥", "👑", "🛠️", "🥔",
           "🐺", "🦅", "🐗", "🐉", "🦁", "🐂", "🏰", "🛡️", "🏹", "💀", "⭐", "🍻",
           "🌻", "🐝", "🐴", "🍎", "🪓", "🎯"]

# ── Týdenní crew cíl → odměna všem (hlavní výhoda být v partě) ──
GOAL_BASE = 8000          # základ týdenního crew cíle
GOAL_PER_MEMBER = 3500    # + za každého člena (větší parta = větší cíl)
GOAL_REWARD = 1500        # odměna za splnění cíle, per člen, 1×/týden (capnuté = ekonomicky bezpečné)


def goal_for(members):
    return GOAL_BASE + max(1, members) * GOAL_PER_MEMBER


# Crew SUB-cíl (peer pressure → reálné suby = $ pro streamera). Odměna = STATUS odznak „Supporter parta",
# ne sedláky → žádný faucet. Týdenní počet subů party odvozený z týdenního sub XP (week_xp − week_farm)/5000.
SUB_GOAL_PER_MEMBER = 1     # cílový počet subů/týden na člena
SUB_GOAL_MIN = 2           # minimum (i malá parta má cíl)


def sub_goal_for(members):
    return max(SUB_GOAL_MIN, members * SUB_GOAL_PER_MEMBER)


def _level(xp):
    """Crew level z all-time crew XP (sqrt křivka, STRMÁ – level je prestige, ne pro každého).
    BEZ stropu (open = trvalá hierarchie, vždy jen jedna #1 parta). Bonus stropuje dřív:
    Lvl 11 = 4 mil XP (farm +5 % max), Lvl 21 = 16 mil XP (sub +40 % max) ≈ jen absolutní špička."""
    return 1 + int(((xp or 0) / 40000.0) ** 0.5)


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
    from datetime import timedelta
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
    t = (tag or "").strip().upper()
    return t if (2 <= len(t) <= 4 and t.isalnum()) else None


def create(conn, uid, username, name, tag):
    if _member(conn, uid):
        raise ValueError("Už jsi v partě – nejdřív z ní odejdi.")
    _check_cooldown(conn, uid)
    name = (name or "").strip()
    if not (3 <= len(name) <= 32):
        raise ValueError("Název party musí mít 3–32 znaků.")
    t = _valid_tag(tag)
    if not t:
        raise ValueError("Tag musí být 2–4 znaky (písmena/čísla).")
    if conn.execute("SELECT 1 FROM crews WHERE name=? COLLATE NOCASE", (name,)).fetchone():
        raise ValueError("Parta s tímhle názvem už existuje.")
    if conn.execute("SELECT 1 FROM crews WHERE tag=?", (t,)).fetchone():
        raise ValueError("Tenhle tag už někdo má.")
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
    emblem = EMBLEMS[abs(hash(t)) % len(EMBLEMS)]
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
    conn.commit()
    return _public(conn, cid, uid)


def _insert_member(conn, crew, uid):
    """Atomický insert člena s cap guardem (fix race check-then-insert). Raise když plno / už člen."""
    try:
        cur = conn.execute(
            "INSERT INTO crew_members (crew_id, user_id, role, week_xp, week, joined_at) "
            "SELECT ?,?,'member',0,?,? WHERE (SELECT COUNT(*) FROM crew_members WHERE crew_id=?) < ?",
            (crew["id"], uid, local_week_id(), now_iso(), crew["id"], crew["member_cap"]))
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
    conn.commit()
    return _public(conn, crew["id"], uid)


def leave(conn, uid):
    m = _member(conn, uid)
    if not m:
        return {"left": True}
    crew_id = m["crew_id"]
    conn.execute("DELETE FROM crew_members WHERE user_id=?", (uid,))
    remaining = conn.execute(
        "SELECT user_id FROM crew_members WHERE crew_id=? ORDER BY joined_at, user_id", (crew_id,)).fetchall()
    if not remaining:
        conn.execute("DELETE FROM crews WHERE id=?", (crew_id,))      # poslední odešel → parta zaniká
    else:
        crew = _crew(conn, crew_id)
        if crew and crew["leader_id"] == uid:                        # vůdce odešel → předej nejstaršímu
            nl = remaining[0]["user_id"]
            conn.execute("UPDATE crews SET leader_id=? WHERE id=?", (nl, crew_id))
            conn.execute("UPDATE crew_members SET role='leader' WHERE crew_id=? AND user_id=?", (crew_id, nl))
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
    conn.execute("DELETE FROM crew_members WHERE user_id=?", (target_uid,))
    notify(conn, target_uid, "🚪", "Odchod z party", "Byl jsi vyhozen z party " + crew["name"] + ".", "#/crews")
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
    conn.execute("UPDATE crews SET motd=? WHERE id=?", ((text or "").strip()[:MOTD_MAX], lm["crew_id"]))
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
    """Člen vyzvedne týdenní odměnu, když parta splnila týdenní cíl. 1×/týden/USER.
    Gate je na users.crew_goal_week (NE na member řádku) → leave+rejoin neobejde (hop-proof)."""
    m = _member(conn, uid)
    if not m:
        raise ValueError("Nejsi v žádné partě.")
    week = local_week_id()
    ur = conn.execute("SELECT crew_goal_week FROM users WHERE id=?", (uid,)).fetchone()
    if ur and ur["crew_goal_week"] == week:
        raise ValueError("Týdenní odměnu už máš vyzvednutou. 🎁")
    crew_id = m["crew_id"]
    week_xp = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN week=? THEN week_xp ELSE 0 END),0) AS x "
        "FROM crew_members WHERE crew_id=?", (week, crew_id)).fetchone()["x"]
    if week_xp < goal_for(_count(conn, crew_id)):
        raise ValueError("Crew cíl tento týden ještě není splněný. Farmařte spolu! 🌾")
    # atomický gate na USERA – 1 claim/týden bez ohledu na hopování part
    if conn.execute(
        "UPDATE users SET crew_goal_week=? WHERE id=? AND (crew_goal_week IS NULL OR crew_goal_week<>?)",
        (week, uid, week)).rowcount == 0:
        raise ValueError("Týdenní odměnu už máš vyzvednutou. 🎁")
    conn.execute("UPDATE crew_members SET claimed_week=? WHERE user_id=?", (week, uid))   # display konzistence
    crew = _crew(conn, crew_id)                       # crew streak: 1×/týden při prvním claimu (cíl je splněný)
    if crew["streak_week"] != week:
        ns = (crew["streak"] + 1) if crew["streak_week"] == _prev_week_id() else 1
        conn.execute("UPDATE crews SET streak=?, streak_week=?, best_streak=MAX(best_streak, ?) WHERE id=?",
                     (ns, week, ns, crew_id))
    add_points(conn, uid, GOAL_REWARD, "Crew týdenní cíl 🤝", xp=False)
    conn.commit()
    out = _public(conn, crew_id, uid)
    out["claimed_now"] = GOAL_REWARD
    return out


# ---------------- veřejný stav ----------------
def leaderboard(conn, uid, limit=50, sort="week"):
    """sort='week' → týdenní XP (farm+sub). 'subs' → SUPPORTER board (all-time sub_xp party).
    'month' → PARTA MĚSÍCE (měsíční sub race, #1 = koruna; měsíčně se resetuje → vždy nová soutěž)."""
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
    return {"week": week, "month": month, "sort": sort, "my_crew_id": m["crew_id"] if m else None,
            "crews": [{"rank": i + 1, "id": r["id"], "name": r["name"], "tag": r["tag"],
                       "emblem": r["emblem"], "members": r["members"], "week_xp": r["week_xp"],
                       "sub_total": r["sub_total"], "month_xp": r["month_xp"],
                       "level": _level(r["xp"]), "goal": goal_for(r["members"])}
                      for i, r in enumerate(rows)]}


def tags(conn):
    """Mapa username_lower → TAG pro VŠECHNY členy crew (pro [TAG] u nicku globálně, cache na frontu)."""
    rows = conn.execute(
        "SELECT LOWER(u.username) AS un, c.tag AS tag FROM crew_members m "
        "JOIN users u ON u.id = m.user_id JOIN crews c ON c.id = m.crew_id").fetchall()
    return {r["un"]: r["tag"] for r in rows}


def state(conn, uid, crew_id):
    return _public(conn, crew_id, uid)


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
        (members_count >= MEMBER_CAP, "👥", "Plná parta"),
    ]
    return [{"icon": ic, "name": nm} for ok, ic, nm in defs if ok]


def _public(conn, crew_id, viewer_uid):
    c = _crew(conn, crew_id)
    if not c:
        return None
    week = local_week_id()
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
                        "week_xp": wx, "contributed": m["contributed"] or 0,
                        "sub_xp": sx, "farm_xp": max(0, (m["contributed"] or 0) - sx), "is_you": is_you})
    chat = []
    if you_member:
        chat = [dict(r) for r in conn.execute(
            "SELECT username, msg, created_at FROM crew_chat WHERE crew_id=? ORDER BY id DESC LIMIT ?",
            (crew_id, CHAT_TAIL)).fetchall()][::-1]
    goal = goal_for(len(members))
    goal_reached = total_week >= goal
    you_claimed = False
    if you_member:                                  # claim evidovaný na USERA (hop-proof)
        _ur = conn.execute("SELECT crew_goal_week FROM users WHERE id=?", (viewer_uid,)).fetchone()
        you_claimed = bool(_ur and _ur["crew_goal_week"] == week)
    lvl = _level(c["xp"])
    week_subs = week_sub_xp // XP_PER_SUB
    sub_goal = sub_goal_for(len(members))
    streak = c["streak"] if c["streak_week"] in (week, _prev_week_id()) else 0   # živý jen pokud tento/minulý týden
    is_leader = c["leader_id"] == viewer_uid
    requests = []
    if is_leader:                                   # žádosti o vstup vidí jen vůdce
        requests = [dict(r) for r in conn.execute(
            "SELECT user_id, username, created_at FROM crew_requests WHERE crew_id=? ORDER BY created_at",
            (crew_id,)).fetchall()]
    return {
        "id": c["id"], "name": c["name"], "tag": c["tag"], "emblem": c["emblem"],
        "leader_id": c["leader_id"], "member_cap": c["member_cap"], "members_count": len(members),
        "xp": c["xp"], "level": lvl,
        "sub_bonus_pct": round(_bonus_frac(lvl, "sub") * 100),
        "farm_bonus_pct": round(_bonus_frac(lvl, "farm") * 100, 1),
        "week_xp": total_week, "week": week,
        "goal": goal, "goal_reached": goal_reached, "goal_reward": GOAL_REWARD,
        "you_claimed": you_claimed,
        "can_claim_goal": bool(you_member and goal_reached and not you_claimed),
        "week_subs": week_subs, "sub_goal": sub_goal, "sub_goal_reached": bool(week_subs >= sub_goal),
        "streak": streak, "best_streak": c["best_streak"] or 0,
        "motd": c["motd"] or "", "private": bool(c["private"]),
        "achievements": _achievements(c, len(members), total_sub),
        "requests": requests,
        "code": c["code"] if you_member else None,        # kód vidí jen členové
        "members": members, "chat": chat,
        "is_member": you_member, "is_leader": is_leader,
    }
