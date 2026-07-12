import deploy


def test_remote_release_switches_only_after_build_and_can_roll_back():
    command = deploy._remote_release_command("webos-test")

    assert 'mv "$app" "$legacy"' in command
    assert 'runuser -u webos -- /opt/webos/venv/bin/pip install -q -r "$release/requirements.txt"' in command
    assert command.index('pip install') < command.index('switch_release "$release"')
    assert 'mv -Tf "$next" "$app"' in command
    assert 'switch_release "$old"' in command
    assert 'http://127.0.0.1:8080/api/monitor/healthz' in command
    assert "NR > 3" in command


def test_release_ids_do_not_reuse_fixed_remote_archive(monkeypatch):
    monkeypatch.setattr(deploy.time, "time_ns", lambda: 123)
    assert deploy._release_id() == "webos-123"
