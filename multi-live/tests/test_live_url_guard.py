from types import SimpleNamespace

import hoststorm.live_url_guard as live_url_guard


def test_strip_seek_removes_checkpoint_seek():
    args = ['-re', '-reconnect', '1', '-ss', '123.500', '-i', 'https://example/live.m3u8']
    assert live_url_guard._strip_seek(args) == ['-re', '-reconnect', '1', '-i', 'https://example/live.m3u8']


def test_probe_is_live_reads_ytdlp_flags(monkeypatch):
    monkeypatch.setattr(live_url_guard.url_sources, '_is_direct_media_url', lambda url: False)
    monkeypatch.setattr(live_url_guard.url_sources, '_yt_base_args', lambda: ['yt-dlp'])
    monkeypatch.setattr(
        live_url_guard.subprocess,
        'run',
        lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"is_live": true, "live_status": "is_live"}', stderr=''),
    )
    assert live_url_guard._probe_is_live('https://youtube.com/watch?v=live123') is True


def test_live_input_does_not_keep_recovery_seek(monkeypatch):
    class DummyManager:
        def __init__(self):
            self.sessions = {}
            self._input_args = lambda session, vertical=False: ['-re', '-ss', '300.000', '-i', session.work_channel['_schedule_source_url']]
            self.start = lambda cid, platforms=None, media=None, trigger='manual', schedule=None: (True, 'ok')

    manager = DummyManager()
    streaming = SimpleNamespace(get_channel=lambda cid: {})
    monkeypatch.setattr(live_url_guard, '_probe_is_live', lambda url: True)
    live_url_guard.install_live_url_guard(manager, streaming)
    schedule = {'source_mode': 'url', 'source_url': 'https://youtube.com/watch?v=live123'}
    manager.start('chan1', ['youtube'], [], 'scheduled', schedule)
    session = SimpleNamespace(
        trigger='scheduled',
        work_channel={'_schedule_source_url': schedule['source_url']},
    )
    args = manager._input_args(session)
    assert '-ss' not in args
    assert session.work_channel['_hs_source_is_live'] is True
