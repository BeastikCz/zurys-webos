"""Admin konzole Kick bota (SedlakBOT): stav, ruční odeslání, log, demo/odpojení."""
import sqlite3

from fastapi import APIRouter, Depends, Request

from .. import kickbot, economy
from ..deps import db_dep, require_user, admin_guard, record_audit
from ..models import BotSendIn, BotToggleIn, SimulateChatIn

router = APIRouter(prefix="/admin/bot", tags=["bot"], dependencies=[Depends(admin_guard)])


@router.get("/status")
def bot_status(conn: sqlite3.Connection = Depends(db_dep)):
    st = kickbot.status(conn)
    st["messages"] = kickbot.recent_messages(conn, 40)
    return st


@router.get("/messages")
def bot_messages(conn: sqlite3.Connection = Depends(db_dep)):
    return kickbot.recent_messages(conn, 40)


@router.get("/diagnose")
def bot_diagnose(conn: sqlite3.Connection = Depends(db_dep)):
    """Otestuje token bota proti Kick API (GET /users) a vrátí přesný důvod 401 – bez posílání zpráv."""
    return kickbot.diagnose(conn)


@router.get("/subscriptions")
def bot_subscriptions(conn: sqlite3.Connection = Depends(db_dep)):
    """Diagnostika: co je reálně přihlášené k odběru u Kicku (jestli vč. subů/resubů/giftů)."""
    return kickbot.list_subscriptions(conn)


@router.post("/send")
def bot_send(data: BotSendIn, request: Request,
             conn: sqlite3.Connection = Depends(db_dep),
             admin: sqlite3.Row = Depends(require_user)):
    res = kickbot.send_message(conn, data.content, kind="manual")
    if res.get("sent"):
        record_audit(conn, admin, request, "bot.send",
                     kickbot.status(conn)["channel"],
                     ("[demo] " if not res.get("real") else "") + data.content[:180])
        conn.commit()
    return res


@router.post("/auto-post")
def bot_auto_post(data: BotToggleIn, request: Request,
                  conn: sqlite3.Connection = Depends(db_dep),
                  admin: sqlite3.Row = Depends(require_user)):
    kickbot.set_auto_post(conn, data.enabled)
    record_audit(conn, admin, request, "bot.auto_post", "drops",
                 "zapnuto" if data.enabled else "vypnuto")
    conn.commit()
    return {"ok": True, "auto_post": data.enabled}


@router.post("/subscribe-events")
def bot_subscribe_events(request: Request,
                         conn: sqlite3.Connection = Depends(db_dep),
                         admin: sqlite3.Row = Depends(require_user)):
    """Aktivuje napojení: přihlásí Kick eventy (sub/resub/gift/follow/chat) na náš webhook."""
    res = kickbot.subscribe_events(conn)
    record_audit(conn, admin, request, "kick.subscribe_events", "events",
                 ("OK" if res.get("ok") else "ERR ") + str(res.get("error", ""))[:160])
    conn.commit()
    return res


@router.post("/demo-connect")
def bot_demo_connect(request: Request,
                     conn: sqlite3.Connection = Depends(db_dep),
                     admin: sqlite3.Row = Depends(require_user)):
    """Demo připojení bota (bez reálného OAuth) – pro lokální vyzkoušení."""
    kickbot.connect_demo(conn)
    record_audit(conn, admin, request, "bot.connect", "demo", "demo režim")
    conn.commit()
    return kickbot.status(conn)


@router.post("/disconnect")
def bot_disconnect(request: Request,
                   conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    kickbot.disconnect(conn)
    record_audit(conn, admin, request, "bot.disconnect", "", "")
    conn.commit()
    return {"ok": True}


@router.post("/simulate-chat")
def bot_simulate_chat(data: SimulateChatIn,
                      conn: sqlite3.Connection = Depends(db_dep)):
    """Demo/test: simuluje zprávu od diváka v chatu → odmění ho za aktivitu.

    Reálné nasazení nahradí toto voláním z Kick chat readeru (kick_username z chatu).
    """
    res = economy.award_chat_by_kick(conn, data.kick_username)
    # zaloguj do bot chatu pro vizuální zpětnou vazbu
    note = (f"💬 {data.kick_username}: +{res['awarded']} sedláků za aktivitu"
            if res.get("awarded") else f"💬 {data.kick_username}: bez odměny "
            f"({res.get('error') or ('cooldown ' + str(res.get('cooldown')) + 's' if res.get('cooldown') else 'strop/0')})")
    kickbot.log_message(conn, kickbot.status(conn)["channel"], "system", note, "system", 0)
    conn.commit()
    return res
