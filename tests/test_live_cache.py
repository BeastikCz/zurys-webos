from app import live


def test_stale_stream_status_does_not_start_parallel_refresh(monkeypatch):
    old_cache = live._cache.copy()
    calls = []
    monkeypatch.setattr(live, "get_setting", lambda *_: "auto")
    monkeypatch.setattr(live, "_detect", lambda _: calls.append(True) or False)
    live._cache.update(at=0, live=True, ok=True)
    assert live._refresh_lock.acquire(blocking=False)
    try:
        assert live.is_live(object()) is True
    finally:
        live._refresh_lock.release()
        live._cache.clear()
        live._cache.update(old_cache)
    assert not calls
