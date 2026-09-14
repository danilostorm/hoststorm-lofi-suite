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
