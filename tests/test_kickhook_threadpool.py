from fastapi import BackgroundTasks
from fastapi.responses import Response

from app.db import get_conn
from app.routers import kickhook


def test_webhook_processing_runs_in_threadpool(client, monkeypatch):
    seen = {}

    async def offload(fn, *args):
        seen["fn"] = fn
        seen["args"] = args
        return Response(status_code=204)

    monkeypatch.setattr(kickhook, "run_in_threadpool", offload)

    response = client.post("/api/kick/webhook", content=b"{}")

    assert response.status_code == 204
    assert seen["fn"] is kickhook._process_webhook
    assert seen["args"][:5] == (b"{}", "", "", "", "")


def test_webhook_requires_message_id():
    response = kickhook._process_webhook(b"{}", "", "", "", "", BackgroundTasks())
    assert response.status_code == 400


def test_locked_webhook_returns_retry_and_rolls_back_dedup(monkeypatch):
    msg_id = "retry-must-not-dedup"
    monkeypatch.setattr(kickhook.kickevents, "verify", lambda *args: True)
    monkeypatch.setattr(kickhook.kickevents, "handle_event", lambda *args: (_ for _ in ()).throw(Exception("database is locked")))
    monkeypatch.setattr(kickhook.time, "sleep", lambda *_: None)
    monkeypatch.setattr(kickhook.alerts, "send", lambda *args, **kwargs: None)

    response = kickhook._process_webhook(b"{}", msg_id, "", "test", "sig", BackgroundTasks())

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    conn = get_conn()
    try:
        assert conn.execute("SELECT 1 FROM webhook_seen WHERE message_id=?", (msg_id,)).fetchone() is None
    finally:
        conn.close()
