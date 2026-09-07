from types import SimpleNamespace

import hoststorm.youtube_playlist as yp


def test_entry_url_builds_watch_url_from_id():
    assert yp._entry_url({'id': 'abc123'}) == 'https://www.youtube.com/watch?v=abc123'


def test_total_limit_uses_playlist_duration_and_stop_before():
    schedule = {'repeat_playlist': False, 'max_duration_minutes': 0, 'stop_before_seconds': 60}
    items = [
        {'duration_seconds': 120.0},
        {'duration_seconds': 180.0},
    ]
    assert yp._total_limit(schedule, items) == 240.0


def test_total_limit_honors_explicit_max_minutes():
    schedule = {'repeat_playlist': True, 'max_duration_minutes': 90, 'stop_before_seconds': 0}
    items = [{'duration_seconds': 120.0}]
    assert yp._total_limit(schedule, items) == 5400.0


def test_restore_order_keeps_saved_order_and_appends_new_items():
    items = [
        {'id': 'a'},
        {'id': 'b'},
        {'id': 'c'},
    ]
    ordered = yp._restore_order(items, ['c', 'a'])
    assert [x['id'] for x in ordered] == ['c', 'a', 'b']


def test_probe_playlist_parses_flat_entries(monkeypatch):
    monkeypatch.setattr(yp, 'validate_remote_url', lambda url: url)
    monkeypatch.setattr(yp, '_playlist_args', lambda: ['yt-dlp'])
    payload = '''{"title":"Minha Playlist","extractor_key":"YoutubeTab","entries":[{"id":"v1","title":"Um","duration":10},{"id":"v2","title":"Dois","duration":20}]}'''
    monkeypatch.setattr(
        yp.subprocess,
        'run',
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=payload, stderr=''),
    )
    result = yp.probe_youtube_playlist('https://www.youtube.com/playlist?list=PL123')
    assert result['ok'] is True
    assert result['count'] == 2
    assert result['duration_seconds'] == 30
    assert result['items'][0]['url'].endswith('watch?v=v1')
