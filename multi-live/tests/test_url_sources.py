from types import SimpleNamespace

import pytest

import hoststorm.db as db
import hoststorm.url_resilience as url_resilience
import hoststorm.url_sources as url_sources


def setup_db(tmp_path):
    db.DB_PATH = tmp_path / 'hoststorm.db'
    db.LEGACY_CHANNELS_PATH = tmp_path / 'channels.json'
    db.init_db()
    web = SimpleNamespace(list_schedules=db.list_schedules, get_schedule=db.get_schedule, save_schedule=db.save_schedule)
    url_sources.install_schedule_db(db, web)
    return db.create_channel('Canal URL')


def schedule_payload(cid, **extra):
    data = {
        'channel_id': cid,
        'name': 'YouTube domingo',
        'kind': 'weekly',
        'weekdays': ['6'],
        'time': '20:30',
        'enabled': True,
        'conflict_policy': 'skip',
        'stop_before_seconds': 60,
        'platforms': ['youtube'],
        'media': [],
        'max_duration_minutes': 0,
        'source_mode': 'url',
        'source_url': 'https://www.youtube.com/watch?v=abc123xyz00',
        'source_title': 'Vídeo de teste',
        'source_duration_seconds': 3600,
        'source_extractor': 'Youtube',
        'source_preview_url': 'https://www.youtube-nocookie.com/embed/abc123xyz00',
    }
    data.update(extra)
    return data


def test_url_schedule_migration_and_roundtrip(tmp_path, monkeypatch):
    cid = setup_db(tmp_path)
    monkeypatch.setattr(url_sources, 'validate_remote_url', lambda value: value)
    sid = db.save_schedule(schedule_payload(cid))
    saved = db.get_schedule(sid)
    assert saved['source_mode'] == 'url'
    assert saved['source_title'] == 'Vídeo de teste'
    assert saved['source_duration_seconds'] == 3600
    assert saved['media'] == []
    with db.connect() as con:
        cols = {r['name'] for r in con.execute('PRAGMA table_info(schedules)').fetchall()}
    assert {'source_mode', 'source_url', 'source_duration_seconds', 'source_preview_url'} <= cols


def test_url_schedule_requires_duration_or_manual_limit(tmp_path, monkeypatch):
    cid = setup_db(tmp_path)
    monkeypatch.setattr(url_sources, 'validate_remote_url', lambda value: value)
    with pytest.raises(ValueError, match='duração'):
        db.save_schedule(schedule_payload(cid, source_duration_seconds=0, max_duration_minutes=0))
    sid = db.save_schedule(schedule_payload(cid, source_duration_seconds=0, max_duration_minutes=90))
    assert db.get_schedule(sid)['max_duration_minutes'] == 90


def test_direct_media_resolver_does_not_need_ytdlp(monkeypatch):
    monkeypatch.setattr(url_sources, 'validate_remote_url', lambda value: value)
    url = 'https://cdn.example.net/media/video.mp4?token=abc'
    assert url_sources.resolve_remote_stream(url) == url


def test_youtube_preview_is_privacy_embed():
    info = {'id': 'abc123xyz00', 'extractor_key': 'Youtube'}
    preview = url_sources._youtube_preview(info, 'https://youtube.com/watch?v=abc123xyz00')
    assert preview == 'https://www.youtube-nocookie.com/embed/abc123xyz00'


def test_private_source_urls_are_blocked():
    with pytest.raises(ValueError, match='privada|local'):
        url_sources.validate_remote_url('http://127.0.0.1/video.mp4')


def test_resolver_falls_back_to_separate_video_audio(monkeypatch):
    monkeypatch.setattr(url_sources, 'validate_remote_url', lambda value: value)
    monkeypatch.setattr(url_sources, '_is_direct_media_url', lambda value: False)
    monkeypatch.setattr(url_sources, '_yt_base_args', lambda: ['yt-dlp'])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        selector = cmd[cmd.index('-f') + 1]
        if selector.startswith('b[') or selector.startswith('best['):
            return SimpleNamespace(returncode=1, stdout='', stderr='Requested format is not available')
        return SimpleNamespace(
            returncode=0,
            stdout='https://video.example/videoplayback\nhttps://audio.example/videoplayback\n',
            stderr='',
        )

    monkeypatch.setattr(url_resilience.subprocess, 'run', fake_run)
    result = url_resilience.resolve_remote_inputs('https://www.youtube.com/watch?v=abc123xyz00')
    assert result['split_av'] is True
    assert result['inputs'] == [
        'https://video.example/videoplayback',
        'https://audio.example/videoplayback',
    ]
    assert len(calls) >= 3


def test_split_source_audio_map_uses_ytdlp_audio_input():
    cmd = [
        'ffmpeg', '-re', '-i', 'video-url', '-re', '-i', 'audio-url',
        '-map', '0:v:0', '-map', '0:a?', '-c:v', 'libx264', '-f', 'flv', 'rtmp://target',
    ]
    fixed = url_resilience._replace_audio_map(cmd, 2)
    audio_map_index = fixed.index('-map', fixed.index('-map') + 1) + 1
    assert fixed[audio_map_index] == '1:a:0'


def test_split_source_external_audio_keeps_priority():
    cmd = [
        'ffmpeg', '-re', '-i', 'video-url', '-re', '-i', 'audio-url',
        '-stream_loop', '-1', '-i', '/audio/music.mp3',
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-f', 'flv', 'rtmp://target',
    ]
    fixed = url_resilience._replace_audio_map(cmd, 2)
    audio_map_index = fixed.index('-map', fixed.index('-map') + 1) + 1
    assert fixed[audio_map_index] == '2:a:0'
