from hoststorm.per_output_rerun import (
    output_rerun_enabled,
    output_rerun_start_seconds,
    rewrite_input_args,
)


def test_output_rerun_falls_back_to_legacy_channel_values():
    channel = {
        'rerun_enabled': '1',
        'rerun_start_seconds': '60',
        'destinations': {'youtube_shorts__mk3': {}},
    }
    assert output_rerun_enabled(channel, 'youtube_shorts__mk3') is True
    assert output_rerun_start_seconds(channel, 'youtube_shorts__mk3') == 60


def test_explicit_output_rerun_overrides_channel_policy():
    channel = {
        'rerun_enabled': '1',
        'rerun_start_seconds': '60',
        'destinations': {
            'youtube_shorts__mk3': {
                'output_rerun_enabled': False,
                'output_rerun_start_seconds': 15,
            },
            'youtube_shorts__dk3': {
                'output_rerun_enabled': True,
                'output_rerun_start_seconds': 90,
            },
        },
    }
    assert output_rerun_enabled(channel, 'youtube_shorts__mk3') is False
    assert output_rerun_start_seconds(channel, 'youtube_shorts__mk3') == 15
    assert output_rerun_enabled(channel, 'youtube_shorts__dk3') is True
    assert output_rerun_start_seconds(channel, 'youtube_shorts__dk3') == 90


def test_first_rerun_play_removes_infinite_loop_but_starts_at_zero():
    args = ['-re', '-stream_loop', '-1', '-i', '/media/MK3.mkv']
    result = rewrite_input_args(args, enabled=True, cycle=0, start_seconds=60)
    assert '-stream_loop' not in result
    assert '-ss' not in result
    assert result[-2:] == ['-i', '/media/MK3.mkv']


def test_next_rerun_play_seeks_only_that_output():
    args = ['-re', '-stream_loop', '-1', '-i', '/media/DK3.mkv']
    result = rewrite_input_args(args, enabled=True, cycle=1, start_seconds=75)
    assert '-stream_loop' not in result
    assert result == ['-re', '-ss', '75.000', '-i', '/media/DK3.mkv']


def test_disabled_custom_rerun_keeps_normal_247_loop():
    args = ['-re', '-stream_loop', '-1', '-i', '/media/MK2.mkv']
    assert rewrite_input_args(args, enabled=False, cycle=5, start_seconds=60) == args


def test_rerun_offset_is_safely_clamped():
    channel = {
        'destinations': {
            'youtube__x': {
                'output_rerun_enabled': True,
                'output_rerun_start_seconds': 999999,
            }
        }
    }
    assert output_rerun_start_seconds(channel, 'youtube__x') == 24 * 3600
