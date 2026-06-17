"""Level Pass: celoživotní milníky podle ÚROVNĚ (ne sezónní – to je Battle Pass).

Dosáhneš úrovně → vyzvedneš exkluzivní kosmetiku, kterou NEJDE koupit (grant-only).
Úroveň jde JEN z poctivého farmení – gambling i placené/gift suby se do `earned_total`
nepočítají (viz deps._NO_EARN_KW), takže milníky = dlouhý grind, ne nákup. Vrchol =
úroveň 100 = trofejový rámeček + reálná cena (claim pingne streamera na Discord, ať ji předá).

Žádná vlastní tabulka: claim = grant kosmetiky → `cosmetic_owns` JE ledger (vlastníš
odměnu = milník vyzvednut). Idempotentní → Discord alert na lvl 100 padne právě jednou.
Server ověří dosaženou úroveň i při claimu (nevěří klientovi).
"""
from .deps import level_info

# (úroveň, kosmetiky k udělení, štítek, ikona). `rewards` = grant_only klíče z cosmetics.CATALOG.
# `irl=True` → po claimu pingni streamera (reálná cena). Earned_total na úroveň: lvl=1+⌊√(et/300)⌋.
MILESTONES = [
    {"level": 10,  "rewards": ["frame_pass10"],                "label": "Učeň",     "icon": "⭐"},
    {"level": 25,  "rewards": ["frame_pass25"],                "label": "Veterán",  "icon": "🔥"},
    {"level": 50,  "rewards": ["frame_pass50"],                "label": "Mistr",    "icon": "💎"},
    {"level": 75,  "rewards": ["frame_pass75"],                "label": "Velmistr", "icon": "🌟"},
    {"level": 100, "rewards": ["frame_legend", "name_legend"], "label": "Legenda",  "icon": "👑", "irl": True},
]
_BY_LEVEL = {m["level"]: m for m in MILESTONES}


def _user_level(user) -> int:
    try:
        et = user["earned_total"] if "earned_total" in user.keys() else 0
    except (KeyError, IndexError, TypeError):
        et = 0
    return level_info(et)["level"]


def _owned(conn, uid: int) -> set:
    return {r["item_key"] for r in conn.execute(
        "SELECT item_key FROM cosmetic_owns WHERE user_id = ?", (uid,))}


def _reward_view(keys: list) -> list:
    """Klíče → [{key,name,cls,type}] pro UI (vynechá neexistující)."""
    from . import cosmetics
    out = []
    for k in keys:
        c = cosmetics.get(k)
        if c:
            out.append({"key": k, "name": c["name"], "cls": c["cls"], "type": c["type"]})
    return out


def status(conn, user) -> dict:
    """Stav Level Passu pro UI: aktuální úroveň + seznam milníků (dosaženo / vyzvednuto)."""
    lvl = _user_level(user)
    owned = _owned(conn, user["id"])
    milestones = []
    for m in MILESTONES:
        primary = m["rewards"][0]
        milestones.append({
            "level": m["level"], "label": m["label"], "icon": m["icon"], "irl": bool(m.get("irl")),
            "rewards": _reward_view(m["rewards"]),
            "reached": lvl >= m["level"],
            "claimed": primary in owned,
        })
    return {"level": lvl, "milestones": milestones,
            "claimable": sum(1 for m in milestones if m["reached"] and not m["claimed"])}


def claim(conn, user, level: int) -> dict:
    """Vyzvedne milník: udělí jeho kosmetiky (grant-only). Idempotentní přes vlastnictví
    primární odměny. Server ověří dosaženou úroveň. Lvl 100 → ping streamera na Discord."""
    from .db import now_iso
    from . import cosmetics
    m = _BY_LEVEL.get(level)
    if not m:
        return {"ok": False, "error": "Neplatný milník."}
    lvl = _user_level(user)
    if lvl < m["level"]:
        return {"ok": False, "error": f"Tenhle milník máš až od úrovně {m['level']}. 💪"}
    uid = user["id"]
    primary = m["rewards"][0]
    if conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id = ? AND item_key = ?",
                    (uid, primary)).fetchone():
        return {"ok": False, "error": "Tenhle milník už máš vyzvednutý. 🏆"}
    granted = []
    for key in m["rewards"]:
        if not cosmetics.get(key):
            continue
        if not conn.execute("SELECT 1 FROM cosmetic_owns WHERE user_id = ? AND item_key = ?",
                            (uid, key)).fetchone():
            conn.execute("INSERT INTO cosmetic_owns (user_id, item_key, acquired_at) VALUES (?,?,?)",
                         (uid, key, now_iso()))
            granted.append(key)
    conn.commit()
    if m.get("irl"):
        _alert_irl(user)
    return {"ok": True, "level": m["level"], "label": m["label"], "icon": m["icon"],
            "irl": bool(m.get("irl")), "granted": granted,
            "reward_names": [c["name"] for c in _reward_view(m["rewards"])]}


def _alert_irl(user) -> None:
    """Úroveň 100 = reálná cena. Pingni streamera na Discord, ať ji předá. Nikdy nesmí shodit claim."""
    try:
        from . import alerts
        try:
            uname = user["username"] or f"#{user['id']}"
        except (KeyError, IndexError, TypeError):
            uname = f"#{user['id']}"
        alerts.send(
            "🏆 ÚROVEŇ 100 — LEGENDA!",
            detail=(f"{uname} právě dosáhl úrovně 100 na zurys.live – vrchol Level Passu!\n"
                    f"Slíbená REÁLNÁ cena (nožík) ho čeká. 🔪 Domluv se s ním na předání "
                    f"(Kick / Discord)."),
            key=f"lvl100-{user['id']}", cooldown=86400, ping=True)
    except Exception:
        pass
