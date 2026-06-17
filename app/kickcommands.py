"""Chat příkazy bota (à la starý SedlakBOT `!sp`).

Příkazy: !sedláci · !leaderboard · !shop · !drop · !predikce · !help.
`handle()` vrátí TEXT odpovědi (None když to není známý příkaz nebo je v cooldownu).
Odeslání do chatu řeší webhook receiver přes BackgroundTask, ať se neblokuje 200.
"""
import time
from typing import Optional

_last: dict = {}     # klíč -> monotonic čas poslední odpovědi (anti-flood)


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _cooldown_ok(key: str, seconds: float) -> bool:
    now = time.monotonic()
    if now - _last.get(key, 0.0) < seconds:
        return False
    _last[key] = now
    return True


def _balance(conn, uname) -> str:
    key = (uname or "").strip().lstrip("@").lower()
    row = conn.execute("SELECT username, points FROM users WHERE kick_username = ?", (key,)).fetchone()
    if not row:
        return f"@{uname}, ještě tě tu nevidím – připoj se na zurys.live a sbírej sedláky! 🌾"
    rank = conn.execute("SELECT COUNT(*) + 1 AS r FROM users WHERE points > ?", (row["points"],)).fetchone()["r"]
    return f"@{uname}, máš {_fmt(row['points'])} sedláků (pořadí #{rank}) 🌾"


def _leaderboard(conn) -> str:
    rows = conn.execute("SELECT username, points FROM users ORDER BY points DESC, id ASC LIMIT 3").fetchall()
    if not rows:
        return "Žebříček je zatím prázdný. 🌾"
    medals = ["🥇", "🥈", "🥉"]
    parts = [f"{medals[i]} {r['username']} ({_fmt(r['points'])})" for i, r in enumerate(rows)]
    return "🏆 " + " · ".join(parts) + " – celý žebříček najdeš na zurys.live"


def _drop(conn) -> str:
    d = conn.execute("SELECT * FROM drops WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    if not d:
        return "Právě neběží žádný drop – sleduj chat, ať ti neuteče! 👀"
    taken = conn.execute("SELECT COUNT(*) AS c FROM drop_claims WHERE drop_id = ?", (d["id"],)).fetchone()["c"]
    left = max(0, d["max_winners"] - taken)
    return f"🎁 Drop právě běží! {_fmt(d['points'])} sedláků, zbývá {left} míst – zadej kód z chatu na zurys.live! ⚡"


def _prediction(conn) -> str:
    p = conn.execute(
        "SELECT * FROM predictions WHERE status IN ('open','locked') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not p:
        return "Právě neběží žádná predikce. 🎯"
    st = "otevřená 🟢" if p["status"] == "open" else "uzamčená 🔒"
    return f"🎯 Predikce ({st}): {p['question']} – vsaď sedláky na zurys.live"


_ALIASES = {
    "balance": {"!sedláci", "!sedlaci", "!sp", "!body", "!balance", "!points"},
    "lb": {"!leaderboard", "!lb", "!top", "!žebříček", "!zebricek"},
    "shop": {"!shop", "!obchod"},
    "drop": {"!drop", "!dropy"},
    "pred": {"!predikce", "!predict", "!prediction"},
    "help": {"!help", "!commands", "!příkazy", "!prikazy", "!zurys"},
}


def handle(conn, uname, content) -> Optional[str]:
    """Vrátí text odpovědi bota na chat příkaz, nebo None (není příkaz / cooldown)."""
    low = (content or "").strip().lower()
    if not low.startswith("!"):
        return None
    word = low.split()[0]
    cmd = next((k for k, al in _ALIASES.items() if word in al), None)
    if not cmd:
        return None
    if cmd == "balance":
        if not _cooldown_ok("bal:" + (uname or "").lower(), 6):   # per-uživatel
            return None
        return _balance(conn, uname)
    if not _cooldown_ok("cmd:" + cmd, 12):                        # broadcast – globální anti-flood
        return None
    if cmd == "lb":
        return _leaderboard(conn)
    if cmd == "shop":
        return "🛒 Utrať své sedláky za skiny a odměny na zurys.live! 👑"
    if cmd == "drop":
        return _drop(conn)
    if cmd == "pred":
        return _prediction(conn)
    if cmd == "help":
        return "📋 Příkazy: !sedláci · !leaderboard · !shop · !drop · !predikce"
    return None
