from flask import Flask

import hoststorm.output_sources as output_sources


def test_source_mode_defaults_to_channel():
    assert output_sources.source_mode({}) == 'channel'
    assert output_sources.source_mode({'output_source_mode': 'invalid'}) == 'channel'
    assert output_sources.source_mode({'output_source_mode': 'local'}) == 'local'
    assert output_sources.source_mode({'output_source_mode': 'url'}) == 'url'


def test_local_output_source_overrides_horizontal_and_vertical():
    channel = {
        'source_mode': 'url',
        'source_url': 'https://example.com/main',
        'video': 'main.mp4',
        'shorts_source_mode': 'same',
        'shorts_video': '',
    }
    destination = {
        'output_source_mode': 'local',
        'output_source_video': 'shorts-2.mp4',
    }
    work = output_sources.apply_source_override(channel, destination)
    assert work['source_mode'] == 'local'
    assert work['video'] == 'shorts-2.mp4'
    assert work['shorts_source_mode'] == 'local'
    assert work['shorts_video'] == 'shorts-2.mp4'
    assert channel['source_mode'] == 'url'


def test_url_output_source_overrides_horizontal_and_vertical():
    channel = {'source_mode': 'local', 'video': 'main.mp4', 'shorts_source_mode': 'same'}
    destination = {
        'output_source_mode': 'url',
        'output_source_url': 'https://www.youtube.com/watch?v=test',
    }
    work = output_sources.apply_source_override(channel, destination)
    assert work['source_mode'] == 'url'
    assert work['source_url'] == 'https://www.youtube.com/watch?v=test'
    assert work['shorts_source_mode'] == 'url'
    assert work['shorts_source_url'] == 'https://www.youtube.com/watch?v=test'


def test_local_source_validation_requires_library_file(tmp_path, monkeypatch):
    monkeypatch.setattr(output_sources, 'VIDEOS_DIR', tmp_path)
    destination = {'output_source_mode': 'local', 'output_source_video': 'live-a.mp4'}
    ok, message = output_sources.validate_source(destination)
    assert ok is False
    assert 'não existe mais' in message

    (tmp_path / 'live-a.mp4').write_bytes(b'test')
    ok, message = output_sources.validate_source(destination)
    assert ok is True
    assert message == ''


def test_rtmp_external_source_is_supported():
    ok, message = output_sources.validate_source({
        'output_source_mode': 'url',
        'output_source_url': 'rtmp://example.com/live/source',
    })
    assert ok is True
    assert message == ''


def test_start_form_persists_selected_library_source_and_new_stream_key(tmp_path, monkeypatch):
    monkeypatch.setattr(output_sources, 'VIDEOS_DIR', tmp_path)
    (tmp_path / 'Mortal Kombat II.mp4').write_bytes(b'test')
    channel = {
        'destinations': {
            'youtube_shorts__mk2': {
                'label': 'Mortal Kombat 2',
                'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2',
                'stream_key': 'old-key',
                'output_source_mode': 'channel',
            }
        }
    }
    app = Flask(__name__)
    with app.test_request_context(method='POST', data={
        'mode': 'local',
        'video': 'Mortal Kombat II.mp4',
        'url': '',
        'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2',
        'stream_key': 'new-key',
    }):
        updated, error = output_sources._update_destination_from_form(
            channel, 'youtube_shorts__mk2', include_transport=True,
        )
    assert error == ''
    assert updated['output_source_mode'] == 'local'
    assert updated['output_source_video'] == 'Mortal Kombat II.mp4'
    assert updated['stream_key'] == 'new-key'


def test_start_form_keeps_saved_stream_key_when_password_field_is_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(output_sources, 'VIDEOS_DIR', tmp_path)
    (tmp_path / 'live.mp4').write_bytes(b'test')
    channel = {
        'destinations': {
            'youtube_shorts__saved': {
                'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2',
                'stream_key': 'encrypted-existing-key',
            }
        }
    }
    app = Flask(__name__)
    with app.test_request_context(method='POST', data={
        'mode': 'local',
        'video': 'live.mp4',
        'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2',
        'stream_key': '',
    }):
        updated, error = output_sources._update_destination_from_form(
            channel, 'youtube_shorts__saved', include_transport=True,
        )
    assert error == ''
    assert updated['stream_key'] == 'encrypted-existing-key'


def test_audio_mode_defaults_to_inherit():
    assert output_sources.audio_mode({}) == 'inherit'
    assert output_sources.audio_mode({'output_audio_mode': 'invalid'}) == 'inherit'
    assert output_sources.audio_mode({'output_audio_mode': 'original'}) == 'original'
    assert output_sources.audio_mode({'output_audio_mode': 'library'}) == 'library'
    assert output_sources.audio_mode({'output_audio_mode': 'url'}) == 'url'


def test_original_audio_override_removes_channel_replacement_audio():
    channel = {
        'audio': 'music.mp3',
        'shorts_audio': '__same__',
    }
    work = output_sources.apply_audio_override(channel, {'output_audio_mode': 'original'})
    assert work['audio'] == ''
    assert work['shorts_audio'] == ''
    assert channel['audio'] == 'music.mp3'


def test_library_audio_override_sets_horizontal_and_vertical_audio():
    work = output_sources.apply_audio_override(
        {'audio': '', 'shorts_audio': '__same__'},
        {'output_audio_mode': 'library', 'output_audio_file': 'soundtrack.mp3'},
    )
    assert work['audio'] == 'soundtrack.mp3'
    assert work['shorts_audio'] == 'soundtrack.mp3'


def test_library_audio_validation_requires_library_file(tmp_path, monkeypatch):
    monkeypatch.setattr(output_sources, 'AUDIOS_DIR', tmp_path)
    destination = {'output_audio_mode': 'library', 'output_audio_file': 'theme.mp3'}
    ok, message = output_sources.validate_audio(destination)
    assert ok is False
    assert 'não existe mais' in message

    (tmp_path / 'theme.mp3').write_bytes(b'test')
    ok, message = output_sources.validate_audio(destination)
    assert ok is True
    assert message == ''


def test_external_audio_mapping_mutes_original_video_audio():
    cmd = [
        'ffmpeg', '-re', '-i', '/media/video.mp4',
        '-map', '0:v:0', '-map', '0:a?',
        '-c:v', 'libx264', '-c:a', 'aac',
        '-f', 'flv', 'rtmp://example.test/live',
    ]
    result = output_sources._inject_external_audio(
        cmd, 'https://cdn.example.test/music.mp3',
    )
    maps = [result[i + 1] for i, token in enumerate(result[:-1]) if token == '-map']
    assert '0:v:0' in maps
    assert '1:a:0' in maps
    assert '0:a?' not in maps
    assert '-shortest' in result
    assert result.count('-i') == 2


def test_start_form_persists_per_output_library_audio(tmp_path, monkeypatch):
    video_dir = tmp_path / 'videos'
    audio_dir = tmp_path / 'audios'
    video_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(output_sources, 'VIDEOS_DIR', video_dir)
    monkeypatch.setattr(output_sources, 'AUDIOS_DIR', audio_dir)
    (video_dir / 'game.mp4').write_bytes(b'video')
    (audio_dir / 'music.mp3').write_bytes(b'audio')
    channel = {
        'destinations': {
            'youtube_shorts__audio': {
                'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2',
                'stream_key': 'key',
                'output_source_mode': 'local',
                'output_source_video': 'game.mp4',
            }
        }
    }
    app = Flask(__name__)
    with app.test_request_context(method='POST', data={
        'mode': 'local',
        'video': 'game.mp4',
        'audio_mode': 'library',
        'audio_file': 'music.mp3',
        'audio_url': '',
    }):
        updated, error = output_sources._update_destination_from_form(
            channel, 'youtube_shorts__audio',
        )
    assert error == ''
    assert updated['output_audio_mode'] == 'library'
    assert updated['output_audio_file'] == 'music.mp3'
    assert updated['output_audio_url'] == ''



def test_audio_mode_supports_original_plus_external_mix():
    assert output_sources.audio_mode({'output_audio_mode': 'mix_library'}) == 'mix_library'
    assert output_sources.audio_mode({'output_audio_mode': 'mix_url'}) == 'mix_url'


def test_mix_library_validation_uses_audio_library(tmp_path, monkeypatch):
    monkeypatch.setattr(output_sources, 'AUDIOS_DIR', tmp_path)
    destination = {'output_audio_mode': 'mix_library', 'output_audio_file': 'podcast.mp3'}
    ok, message = output_sources.validate_audio(destination)
    assert ok is False
    assert 'não existe mais' in message
    (tmp_path / 'podcast.mp3').write_bytes(b'audio')
    ok, message = output_sources.validate_audio(destination)
    assert ok is True
    assert message == ''


def test_podcast_mix_maps_original_and_external_with_ducking():
    cmd = [
        'ffmpeg', '-re', '-i', '/media/game.mp4',
        '-map', '0:v:0', '-map', '0:a?',
        '-vf', 'scale=1280:720',
        '-c:v', 'libx264', '-c:a', 'aac',
        '-f', 'flv', 'rtmp://example.test/live',
    ]
    destination = {
        'output_audio_mode': 'mix_url',
        'output_audio_mix_profile': 'podcast',
    }
    result = output_sources._inject_mixed_audio(
        cmd, 'https://cdn.example.test/podcast.mp3', destination,
    )
    assert result.count('-i') == 2
    assert '-filter_complex' in result
    graph = result[result.index('-filter_complex') + 1]
    assert 'sidechaincompress' in graph
    assert 'loudnorm=I=-16' in graph
    assert 'asplit=2[podcast_sidechain][podcast_mix]' in graph
    assert '[game][podcast_sidechain]sidechaincompress' in graph
    assert '[ducked][podcast_mix]amix' in graph
    assert '[podcast]' not in graph
    maps = [result[i + 1] for i, token in enumerate(result[:-1]) if token == '-map']
    assert '0:v:0' in maps
    assert '[aout]' in maps
    assert '0:a?' not in maps
    assert '-shortest' in result


def test_balanced_mix_uses_equal_default_gains_without_ducking():
    cmd = [
        'ffmpeg', '-i', '/media/game.mp4',
        '-map', '0:v:0', '-map', '0:a?',
        '-c:a', 'aac', '-f', 'flv', 'rtmp://example.test/live',
    ]
    destination = {
        'output_audio_mode': 'mix_library',
        'output_audio_mix_profile': 'balanced',
    }
    result = output_sources._inject_mixed_audio(cmd, '/media/podcast.mp3', destination)
    graph = result[result.index('-filter_complex') + 1]
    assert 'sidechaincompress' not in graph
    assert graph.count('volume=-6.0dB') == 2
    assert 'amix=inputs=2:duration=first' in graph


def test_manual_mix_clamps_and_uses_saved_gains():
    destination = {
        'output_audio_mix_profile': 'manual',
        'output_audio_original_gain_db': -18,
        'output_audio_external_gain_db': 3,
    }
    assert output_sources._mix_gains(destination) == (-18.0, 3.0)
    assert output_sources._safe_gain_db(-99, 0) == -30.0
    assert output_sources._safe_gain_db(99, 0) == 12.0


def test_form_persists_mix_profile_and_manual_gains(tmp_path, monkeypatch):
    monkeypatch.setattr(output_sources, 'VIDEOS_DIR', tmp_path)
    monkeypatch.setattr(output_sources, 'AUDIOS_DIR', tmp_path)
    (tmp_path / 'game.mp4').write_bytes(b'video')
    (tmp_path / 'podcast.mp3').write_bytes(b'audio')
    channel = {
        'destinations': {
            'youtube__mix': {
                'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2',
                'stream_key': 'key',
            }
        }
    }
    app = Flask(__name__)
    with app.test_request_context(method='POST', data={
        'mode': 'local',
        'video': 'game.mp4',
        'audio_mode': 'mix_library',
        'audio_file': 'podcast.mp3',
        'audio_mix_profile': 'manual',
        'audio_original_gain_db': '-15',
        'audio_external_gain_db': '-2',
    }):
        updated, error = output_sources._update_destination_from_form(channel, 'youtube__mix')
    assert error == ''
    assert updated['output_audio_mode'] == 'mix_library'
    assert updated['output_audio_mix_profile'] == 'manual'
    assert updated['output_audio_original_gain_db'] == -15.0
    assert updated['output_audio_external_gain_db'] == -2.0



def test_youtube_audio_resolution_prefers_audio_only_and_caps_video_fallback(monkeypatch):
    captured = {}

    class Proc:
        returncode = 0
        stdout = 'https://rr.example.test/audio-only.m4a\n'
        stderr = ''

    monkeypatch.setattr(output_sources.shutil, 'which', lambda name: '/usr/bin/yt-dlp')

    def fake_run(cmd, **kwargs):
        captured['cmd'] = list(cmd)
        captured['kwargs'] = dict(kwargs)
        return Proc()

    monkeypatch.setattr(output_sources.subprocess, 'run', fake_run)
    resolved = output_sources._resolve_audio_url('https://www.youtube.com/watch?v=test123')
    assert resolved == 'https://rr.example.test/audio-only.m4a'
    cmd = captured['cmd']
    selector = cmd[cmd.index('-f') + 1]
    assert selector == output_sources.EXTERNAL_AUDIO_SELECTOR
    assert selector.startswith('bestaudio')
    assert 'height<=144' in selector
    assert 'bestaudio/best' not in selector
    assert cmd[cmd.index('--socket-timeout') + 1] == '15'
    assert cmd[cmd.index('--retries') + 1] == '3'


def test_direct_audio_url_skips_ytdlp(monkeypatch):
    monkeypatch.setattr(
        output_sources.subprocess,
        'run',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('yt-dlp não deveria ser chamado')),
    )
    url = 'https://cdn.example.test/podcast.mp3'
    assert output_sources._resolve_audio_url(url) == url
