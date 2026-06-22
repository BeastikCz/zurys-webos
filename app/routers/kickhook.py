"""Kick webhook receiver: POST /api/kick/webhook.

Kick sem posílá eventy (sub/resub/gift/follow/chat). Ověříme podpis, odmítneme
podvrh (403), jinak přičteme sedláky a VŽDY vrátíme 200 (jinak nás Kick odhlásí).
Dedup přes Kick-Event-Message-Id (Kick retryuje).
"""
import json
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Request, Response

from ..db import get_conn, now_iso
from .. import kickevents, kickbot, alerts

router = APIRouter(tags=["kick-webhook"])

# Idempotence webhooku je PERZISTENTNÍ v tabulce webhook_seen (přežije restart/deploy) – na rozdíl
# od dřívější paměťové dedup, kterou restart vynuloval a Kickův replay tak přičetl body znovu.
PRUNE_EVERY = 2000        # po kolika zpracováních zkusit úklid starých ID
SEEN_TTL_DAYS = 3         # ID se drží 3 dny (Kick retryuje v řádu minut) → pak se smaže
_since_prune = 0


def _maybe_prune(conn) -> None:
    """Občas (každých PRUNE_EVERY zpracování) smaž ID webhooků starší než SEEN_TTL_DAYS, ať tabulka neroste."""
    global _since_prune
    _since_prune += 1
    if _since_prune < PRUNE_EVERY:
        return
    _since_prune = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    conn.execute("DELETE FROM webhook_seen WHERE created_at < ?", (cutoff,))


def _send_command_reply(text: str) -> None:
    """Odešle odpověď bota na chat příkaz – mimo request (webhook vrátí 200 hned, neblokuje se)."""
    try:
        conn = get_conn()
        try:
            kickbot.send_message(conn, text, kind="command")
        finally:
            conn.close()
    except Exception as e:  # pragma: no cover
        print("[kick-webhook] reply send error:", e)


@router.post("/kick/webhook")
async def kick_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    h = request.headers
    msg_id = h.get("kick-event-message-id", "")
    ts = h.get("kick-event-message-timestamp", "")
    etype = h.get("kick-event-type", "")
    sig = h.get("kick-event-signature", "")

    # 1) ověření podpisu – jen reálný Kick smí spustit přičtení bodů
    if not kickevents.verify(msg_id, ts, body, sig):
        return Response(status_code=403)

    # 2) parse payload – chyba NESMÍ vrátit 5xx (jinak Kick event odhlásí)
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}

    # 3) dedup + zpracování ATOMICKY v jedné transakci: claim message_id i připsání bodů se commitnou
    # SPOLEČNĚ. Když zpracování spadne, rollback vrátí i claim → Kickův retry to zkusí znovu (žádný
    # ztracený event). Duplikát/replay (ID už v tabulce, i po restartu) se přeskočí bez dvojího připsání.
    # RETRY na přechodný „database is locked" (1-writer SQLite); čerstvý conn = bezpečný rollback.
    result, last_exc, duplicate = None, None, False
    for attempt in range(3):
        try:
            conn = get_conn()
            try:
                if msg_id and conn.execute(
                    "INSERT OR IGNORE INTO webhook_seen (message_id, created_at) VALUES (?, ?)",
                    (msg_id, now_iso()),
                ).rowcount == 0:
                    conn.commit()
                    duplicate = True
                    break
                result = kickevents.handle_event(conn, etype, payload)
                _maybe_prune(conn)
                conn.commit()
            finally:
                conn.close()
            last_exc = None
            break
        except Exception as e:  # pragma: no cover
            last_exc = e
            if "database is locked" in str(e).lower() and attempt < 2:
                time.sleep(0.15 * (attempt + 1))   # 150 ms, pak 300 ms
                continue
            break

    if duplicate:
        return Response(status_code=200)        # už zpracováno (retry/replay) – nepřičítat znovu
    if last_exc is None:
        if etype and etype != "chat.message.sent":   # diagnostika: sub/resub/gift/follow eventy
            print(f"[kick-webhook] event {etype} -> {result}")
        if result and result.get("reply"):           # chat příkaz → bot odpoví po vrácení 200
            background.add_task(_send_command_reply, result["reply"])
    else:
        e = last_exc
        print("[kick-webhook] handle error:", etype, e)
        # přechodný lock i po retry → tichý alert (hrubý klíč, delší cooldown, bez pingu),
        # ať to nezaplaví Discord; reálná sustained contention se i tak 1×/15 min ozve.
        locked = "database is locked" in str(e).lower()
        alerts.send("🟠 Kick webhook chyba: " + str(etype),
                    detail=type(e).__name__ + ": " + str(e)[:300],
                    key=("kick:db-locked" if locked else "kick:" + str(etype)),
                    cooldown=(900 if locked else 300))

    return Response(status_code=200)
