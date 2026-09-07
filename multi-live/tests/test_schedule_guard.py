from types import SimpleNamespace

import hoststorm.schedule_guard as schedule_guard


class DummyManager:
    def __init__(self):
        self.calls = []

    def start(self, cid, platforms=None, media=None, trigger='manual', schedule=None):
        self.calls.append((cid, list(platforms or []), trigger))
        return True, 'started'


def _channel():
    return {
        'destinations': {
            'kick': {'label': 'Kick', 'rtmp_url': 'rtmps://example/app', 'stream_key': 'abc'},
            'custom': {'label': 'Custom RTMP', 'rtmp_url': '', 'stream_key': ''},
        }
    }


def test_scheduled_start_ignores_unconfigured_destination(monkeypatch):
    manager = DummyManager()
    audits = []
    streaming = SimpleNamespace(
        get_channel=lambda cid: _channel(),
        audit=lambda *args, **kwargs: audits.append((args, kwargs)),
    )
    monkeypatch.setattr(schedule_guard, 'build_target', lambda url, key: bool(url and key))
    schedule_guard.install_schedule_platform_guard(manager, streaming)

    ok, msg = manager.start(
        'chan1', ['kick', 'custom'], [], 'scheduled', {'id': 'sched1', 'platforms': ['kick', 'custom']}
    )

    assert ok is True
    assert manager.calls[-1][1] == ['kick']
    assert 'Custom RTMP' in msg
    assert audits


def test_scheduled_start_fails_only_when_no_valid_destination(monkeypatch):
    manager = DummyManager()
    streaming = SimpleNamespace(get_channel=lambda cid: _channel(), audit=lambda *a, **k: None)
    monkeypatch.setattr(schedule_guard, 'build_target', lambda url, key: bool(url and key))
    schedule_guard.install_schedule_platform_guard(manager, streaming)

    ok, msg = manager.start('chan1', ['custom'], [], 'scheduled', {'id': 'sched2'})

    assert ok is False
    assert 'Nenhuma plataforma' in msg
    assert manager.calls == []
