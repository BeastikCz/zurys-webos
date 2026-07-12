from fastapi.responses import Response

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
