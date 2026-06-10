"""Kick webhook receiver: POST /api/kick/webhook.

Kick sem posílá eventy (sub/resub/gift/follow/chat). Ověříme podpis, odmítneme
podvrh (403), jinak přičteme sedláky a VŽDY vrátíme 200 (jinak nás Kick odhlásí).
Dedup přes Kick-Event-Message-Id (Kick retryuje).
"""
import json
from collections import deque

from fastapi import APIRouter, BackgroundTasks, Request, Response

from ..db import get_conn
from .. import kickevents, kickbot, alerts

router = APIRouter(tags=["kick-webhook"])

_seen = deque(maxlen=1000)      # idempotence – ID zpracovaných zpráv
_seen_set = set()


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

    # 2) dedup (Kick posílá retry až 3×)
    if msg_id and msg_id in _seen_set:
        return Response(status_code=200)
    if msg_id:
        if len(_seen) >= _seen.maxlen:
            _seen_set.discard(_seen[0])
        _seen.append(msg_id)
        _seen_set.add(msg_id)

    # 3) zpracování – chyba NESMÍ vrátit 5xx (jinak Kick event odhlásí)
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}
    try:
        conn = get_conn()
        try:
            result = kickevents.handle_event(conn, etype, payload)
            conn.commit()
            if etype and etype != "chat.message.sent":   # diagnostika: sub/resub/gift/follow eventy
                print(f"[kick-webhook] event {etype} -> {result}")
        finally:
            conn.close()
        if result and result.get("reply"):     # chat příkaz → bot odpoví po vrácení 200
            background.add_task(_send_command_reply, result["reply"])
    except Exception as e:  # pragma: no cover
        print("[kick-webhook] handle error:", etype, e)
        alerts.send("🟠 Kick webhook chyba: " + str(etype),
                    detail=type(e).__name__ + ": " + str(e)[:300],
                    key="kick:" + str(etype), cooldown=300)

    return Response(status_code=200)
