from hoststorm.cluster_v5 import _audio_for_output, _media_for_output, _remote_snapshot, _score, output_placement


def test_output_placement_defaults_to_channel_local():
    channel = {'node_mode': 'local', 'destinations': {'youtube__x': {}}}
    assert output_placement(channel, 'youtube__x') == ('local', '')


def test_output_specific_node_overrides_channel_auto():
    channel = {
        'node_mode': 'auto',
        'destinations': {
            'youtube__x': {'output_node_mode': 'specific', 'output_node_id': 'node-b'}
        },
    }
    assert output_placement(channel, 'youtube__x') == ('specific', 'node-b')


def test_output_local_overrides_channel_specific():
    channel = {
        'node_mode': 'specific',
        'node_id': 'node-a',
        'destinations': {'youtube__x': {'output_node_mode': 'local'}},
    }
    assert output_placement(channel, 'youtube__x') == ('local', '')


def test_auto_score_penalizes_load_and_streams():
    idle = {'priority': 100, 'cpu': 10, 'ram': 20, 'gpu': 0, 'active_streams': 0}
    busy = {'priority': 100, 'cpu': 80, 'ram': 80, 'gpu': 70, 'active_streams': 5}
    assert _score(idle) < _score(busy)


def test_remote_snapshot_forces_local_to_prevent_recursive_dispatch():
    channel = {
        'id': 'c1',
        'node_mode': 'auto',
        'destinations': {
            'youtube__x': {'output_node_mode': 'specific', 'output_node_id': 'node-a'}
        },
    }
    snap = _remote_snapshot(channel, 'youtube__x')
    assert snap['node_mode'] == 'local'
    assert snap['destinations']['youtube__x']['output_node_mode'] == 'local'
    assert snap['destinations']['youtube__x']['output_node_id'] == ''
    assert channel['node_mode'] == 'auto'


def test_media_for_individual_local_source():
    channel = {
        'video': 'main.mp4',
        'destinations': {
            'youtube__mk3': {
                'output_source_mode': 'local',
                'output_source_video': 'MK3.mp4',
            }
        },
    }
    assert _media_for_output(channel, 'youtube__mk3') == ['MK3.mp4']


def test_audio_for_individual_library_source():
    channel = {
        'audio': 'channel.mp3',
        'destinations': {
            'youtube__mk3': {
                'output_audio_mode': 'library',
                'output_audio_file': 'MK3-theme.mp3',
            }
        },
    }
    assert _audio_for_output(channel, 'youtube__mk3') == ['MK3-theme.mp3']


def test_audio_for_original_or_url_does_not_sync_channel_audio():
    channel = {
        'audio': 'channel.mp3',
        'destinations': {
            'youtube__original': {'output_audio_mode': 'original'},
            'youtube__url': {
                'output_audio_mode': 'url',
                'output_audio_url': 'https://example.com/audio.mp3',
            },
        },
    }
    assert _audio_for_output(channel, 'youtube__original') == []
    assert _audio_for_output(channel, 'youtube__url') == []


def test_audio_inherit_uses_vertical_override_when_present():
    channel = {
        'audio': 'horizontal.mp3',
        'shorts_audio': 'vertical.mp3',
        'destinations': {
            'youtube_shorts__one': {
                'platform': 'youtube_shorts',
                'mode': 'vertical',
            }
        },
    }
    assert _audio_for_output(channel, 'youtube_shorts__one') == ['vertical.mp3']
